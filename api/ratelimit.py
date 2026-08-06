"""探索 API 的安全防护层：客户端 IP 识别、限流、黑名单（含自动封禁）。

设计目标（云端 demo 场景）：
- 防止单个客户端把昂贵的探索计算（社区发现 / 嵌入 / PageRank 等）拖垮服务器。
- 两档限流：
    * 一般请求（浏览、元数据）——宽松。
    * 探索计算 POST /api/board/* ——严格（这是真正耗资源的入口）。
- 黑名单：环境变量种子（永久封禁）+ 自动封禁（短时间内多次超限即临时封禁）。
- 可选持久化：自动封禁可写入 JSON 文件，重启后仍生效。

全部阈值通过环境变量配置，无需改代码即可调参；纯标准库实现，无额外依赖。
"""
import os
import time
from collections import defaultdict


def _env_int(env: str, default: int) -> int:
    try:
        return int(os.environ.get(env, default))
    except (TypeError, ValueError):
        return default


def _env_bool(env: str, default: bool = False) -> bool:
    v = os.environ.get(env)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class SecurityConfig:
    """从环境变量读取安全阈值，带合理默认值（云端 demo 取向）。"""

    def __init__(self):
        # 是否信任反向代理转发头（云端 LB/CDN 后务必开启）
        self.trust_proxy = _env_bool("GOTY_TRUST_PROXY", True)

        # 一般请求限流（所有 HTTP 方法 + 路径）
        self.general_max = _env_int("GOTY_RATE_LIMIT_MAX", 200)
        self.general_window = _env_int("GOTY_RATE_WINDOW", 60)

        # 探索计算限流（仅 POST /api/board/*，重负载）
        self.board_max = _env_int("GOTY_BOARD_LIMIT_MAX", 8)
        self.board_window = _env_int("GOTY_BOARD_WINDOW", 60)

        # 自动封禁：在 autoban_window 秒内累计超限达到阈值即临时封禁
        self.autoban_violations = _env_int("GOTY_AUTOBAN_VIOLATIONS", 5)
        self.autoban_seconds = _env_int("GOTY_AUTOBAN_SECONDS", 3600)

        # 黑名单：环境变量种子（逗号分隔，永久）；可选持久化文件
        self.blacklist_file = os.environ.get("GOTY_BLACKLIST_FILE", "")
        seed = os.environ.get("GOTY_BLACKLIST", "")
        self.seed_blacklist = [ip.strip() for ip in seed.split(",") if ip.strip()]


def get_client_ip(request, trust_proxy: bool = True) -> str:
    """提取真实客户端 IP。

    信任代理时优先读取 X-Forwarded-For 首段（原始客户端）、其次 X-Real-IP；
    否则退回直连对端。注意：部署时应在边缘节点剥离客户端伪造的 XFF，避免绕过。
    """
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            first = xff.split(",")[0].strip()
            if first:
                return first
        xri = request.headers.get("x-real-ip")
        if xri:
            return xri.strip()
    client = getattr(request, "client", None)
    return client.host if client else "0.0.0.0"


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


class Blacklist:
    """IP 黑名单：永久种子 + 临时自动封禁（可选持久化到 JSON）。"""

    def __init__(self, seed: list = None, file_path: str = ""):
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
            with open(self.file_path, "r", encoding="utf-8") as f:
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

    def register_violation(self, ip: str, autoban_violations: int,
                           autoban_seconds: int) -> bool:
        """记录一次超限；达到阈值且尚未封禁则临时封禁，返回是否刚刚封禁。"""
        self.violations[ip] += 1
        if (self.violations[ip] >= autoban_violations
                and not self.is_blacklisted(ip)):
            self.add(ip, autoban_seconds)
            return True
        return False
