"""探索 API 的安全底层原语：客户端 IP 识别、限流、黑名单（含自动封禁）。

设计目标（云端 demo 场景）：
- 防止单个客户端把昂贵的探索计算（社区发现 / 嵌入 / PageRank 等）拖垮服务器。
- 两档限流：
    * 一般请求（浏览、元数据）——宽松。
    * 探索计算 POST /api/board/* ——严格（这是真正耗资源的入口）。
- 黑名单：环境变量种子（永久封禁）+ 自动封禁（短时间内多次超限即临时封禁）。
- 可选持久化：自动封禁可写入 JSON 文件，重启后仍生效。

阈值由 :class:`api.config.Settings` 统一提供，经 :class:`api.security.SecurityContext`
组装；本模块只提供无状态、可单测的纯逻辑，不直接读环境变量。
"""

import ipaddress
import os
import time
from collections import defaultdict
from typing import Protocol, runtime_checkable


def _parse_networks(raw: str) -> list:
    """把 ``GOTY_TRUSTED_PROXIES``（逗号分隔的 IP / CIDR）解析为网络对象列表。"""
    nets = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            # 非法条目忽略：宁可少信任一个代理，也不因配置笔误而放行伪造头
            continue
    return nets


def _in_networks(ip: str, nets: list) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in nets)


def _valid_ip(ip: str) -> bool:
    try:
        ipaddress.ip_address(ip)
    except ValueError:
        return False
    return True


def get_client_ip(request, trust_proxy: bool = True, trusted_proxies: str = "") -> str:
    """提取真实客户端 IP（**仅在直连方是可信代理时才采信 XFF**）。

    ``X-Forwarded-For`` / ``X-Real-IP`` 是**客户端可任意伪造**的请求头。旧实现无条件
    取 XFF 首段，攻击者发一个 ``X-Forwarded-For: 1.2.3.4`` 就能任意切换身份 —— 限流、
    黑名单、自动封禁全部失效；更可把任意第三方 IP 打成黑名单（针对正常用户的 DoS），
    并污染审计记录。

    因此这里以**直连对端**为准做信任判定：

    - 未开启 ``trust_proxy``、或未配置 ``trusted_proxies``、或直连对端不在可信网段内
      → 一律不采信 XFF/XRI，返回直连对端（客户端伪造无效）。
    - 直连对端确为可信代理 → 从 XFF 链**由右向左**跳过可信代理自己追加的条目，
      取第一个非可信地址作为真实客户端；全链皆可信或格式非法则回落直连对端。

    运维：反代部署时把边缘/反代的地址配进 ``GOTY_TRUSTED_PROXIES``（如 ``10.0.0.0/8``）。
    """
    client = getattr(request, "client", None)
    direct = client.host if client else "0.0.0.0"

    nets = _parse_networks(trusted_proxies) if trust_proxy else []
    if not nets or not _in_networks(direct, nets):
        # 直连方不是可信代理 -> 任何 XFF/XRI 都可能是伪造，一律忽略
        return direct

    xff = request.headers.get("x-forwarded-for")
    if xff:
        # XFF 形如 "client, proxy1, proxy2"：最左为原始客户端（可伪造），
        # 最右为最近一跳代理所追加（可信）。故从右往左找第一个不可信地址。
        chain = [p.strip() for p in xff.split(",") if p.strip()]
        for candidate in reversed(chain):
            if _valid_ip(candidate) and not _in_networks(candidate, nets):
                return candidate
    xri = request.headers.get("x-real-ip")
    if xri and _valid_ip(xri.strip()) and not _in_networks(xri.strip(), nets):
        return xri.strip()
    return direct


class Limiter:
    """固定窗口计数器限流（单进程内存版，demo 足够）。

    返回 (allowed, retry_after)：allowed 为 False 时 retry_after 为建议重试秒数。
    """

    def __init__(self, max_req: int, window: int):
        self.max = max(1, max_req)
        self.window = max(1, window)
        self._buckets: dict = {}  # key -> [count, window_start_epoch]

    def check(self, key: str):
        now = time.time()
        cnt, start = self._buckets.get(key, (0, now))
        if now - start >= self.window:
            return True, 0
        if cnt >= self.max:
            retry_after = int(self.window - (now - start)) + 1
            return False, max(1, retry_after)
        return True, 0

    def hit(self, key: str):
        now = time.time()
        cnt, start = self._buckets.get(key, (0, now))
        if now - start >= self.window:
            cnt, start = 0, now
        self._buckets[key] = (cnt + 1, start)

    def check_and_hit(self, key: str) -> tuple[bool, int]:
        """原子「判定并计数」：允许则立即计数，返回 ``(allowed, retry_after)``。

        必须成对使用 ``check`` + ``hit`` 才能生效——``check`` 是纯读、从不写桶，
        漏调 ``hit`` 会让限流**完全失效**（计数器恒为 0）。故提供本方法作为默认入口，
        避免调用方踩这个坑。
        """
        allowed, retry_after = self.check(key)
        if allowed:
            self.hit(key)
        return allowed, retry_after


class Blacklist:
    """IP 黑名单：永久种子 + 临时自动封禁（可选持久化到 JSON）。"""

    def __init__(self, seed: list | None = None, file_path: str = ""):
        self.file_path = file_path or ""
        self.permanent: set = set(seed or [])
        self.temp: dict = {}  # ip -> expiry_epoch（0 表示永久）
        self.violations: dict = defaultdict(int)
        self._load()

    def _load(self):
        if not self.file_path:
            return
        try:
            import json

            with open(self.file_path, encoding="utf-8") as f:
                data = json.load(f)
            for ip, exp in (data.get("temp", {}) or {}).items():
                if exp == 0 or exp > time.time():
                    self.temp[ip] = exp
        except FileNotFoundError:
            pass
        except Exception:
            pass

    def _save(self):
        if not self.file_path:
            return
        try:
            import json

            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump({"temp": self.temp}, f)
        except Exception:
            pass

    def is_blacklisted(self, ip: str) -> bool:
        if ip in self.permanent:
            return True
        exp = self.temp.get(ip)
        if exp is None:
            return False
        if exp != 0 and exp <= time.time():
            del self.temp[ip]
            self._save()
            return False
        return True

    def add(self, ip: str, seconds: int = 0):
        self.temp[ip] = 0 if seconds <= 0 else time.time() + seconds
        self._save()

    def register_violation(self, ip: str, autoban_violations: int, autoban_seconds: int) -> bool:
        """记录一次超限；达到阈值且尚未封禁则临时封禁，返回是否刚刚封禁。"""
        self.violations[ip] += 1
        if self.violations[ip] >= autoban_violations and not self.is_blacklisted(ip):
            self.add(ip, autoban_seconds)
            return True
        return False


@runtime_checkable
class RateLimiter(Protocol):
    """限流原语协议：``check`` 问「是否放行」，``hit`` 记「本次消耗配额」。

    替换为 Redis / 集中式实现时，只需提供满足该协议的类，并在
    :func:`create_rate_limiter` 中返回它，其余代码（SecurityContext / 中间件）零改动。
    """

    def check(self, key: str) -> tuple[bool, int]:
        """返回 ``(allowed, retry_after)``；``allowed=False`` 时 ``retry_after`` 为建议重试秒数。"""
        ...

    def hit(self, key: str) -> None:
        """记录该 key 的一次请求（消耗一次配额）。"""
        ...


def create_rate_limiter(max_req: int, window: int, *, redis_url: str | None = None) -> RateLimiter:
    """限流实现工厂：默认内存 :class:`Limiter`；配置 ``redis_url`` 时返回 :class:`RedisLimiter`。

    这是「无缝替换」的扩展点——未来引入 Redis / 集中式限流，只需在此返回对应实现，
    ``SecurityContext`` 与中间件按协议调用，无需改动。
    """
    if redis_url:
        return RedisLimiter(redis_url, max_req, window)
    return Limiter(max_req, window)


class RedisLimiter:
    """基于 Redis 的固定窗口限流（**参考实现**，演示无缝替换）。

    需先 ``uv pip install redis`` 并配置 ``GOTY_RATE_LIMIT_REDIS_URL``。用 Lua 脚本做
    原子自增 + 首次过期，保证并发下窗口边界一致；``check`` / ``hit`` 签名与 :class:`Limiter`
    完全一致，因此可零改动替换。
    """

    def __init__(self, redis_url: str, max_req: int, window: int, prefix: str = "goty:rl:") -> None:
        try:
            import redis
        except ImportError as exc:  # 仅在真正启用 Redis 限流时才要求依赖
            raise RuntimeError("使用 Redis 限流需先安装依赖：uv pip install redis") from exc
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.max = max(1, max_req)
        self.window = max(1, window)
        self._prefix = prefix
        self._script = self._client.register_script(
            "local cnt = redis.call('incr', KEYS[1])\n"
            "if cnt == 1 then redis.call('expire', KEYS[1], ARGV[1]) end\n"
            "return cnt"
        )

    def check(self, key: str) -> tuple[bool, int]:
        cnt = int(self._client.get(self._prefix + key) or 0)
        if cnt >= self.max:
            ttl = self._client.ttl(self._prefix + key)
            retry = max(1, ttl if ttl and ttl > 0 else self.window)
            return False, retry
        return True, 0

    def hit(self, key: str) -> None:
        self._script(keys=[self._prefix + key], args=[self.window])
