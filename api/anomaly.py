"""请求源异常判定模块。

职责：对进入的请求源（目前以客户端 IP 为主键）做轻量、可插拔的异常判定。命中规则时
返回原因，并触发处置动作（如加入黑名单封禁一段时间）。

设计原则：
- **纯内存、无阻塞**：每个请求只做 O(窗口内计数) 的滑动窗口检查，远快于任何 I/O，
  可安全地在中间件主路径上同步调用。
- **规则可插拔**：新增异常策略（UA 异常、路径扫描、突发分布…）只需新增一个 Rule 并注册，
  中间件零改动。
- **处置解耦**：本模块只产出「异常结论 + 原因」，具体封禁动作委托给现有
  :class:`api.ratelimit.Blacklist`，复用其已验证的持久化 / 解封逻辑。

默认策略（FrequencyRule）：同一 IP 在 ``window`` 秒内的请求数超过 ``max`` 即判定异常，
命中后建议封禁 ``ban_seconds``（如 86400 = 24h）。
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Protocol


class AnomalyRule(Protocol):
    """异常规则协议：给定某 IP 的「请求时间戳滑动窗口」判定是否异常。"""

    name: str

    def evaluate(self, timestamps: deque[float]) -> bool: ...


class FrequencyRule:
    """滑动窗口频率规则：单 IP 在 ``window`` 秒内请求次数超过 ``max`` 即异常。

    命中后由 :class:`AnomalyDetector` 触发封禁，封禁时长取本规则的 ``ban_seconds``。
    """

    name = "frequency"

    def __init__(self, max_requests: int, window: int, ban_seconds: int) -> None:
        self.max = max(1, max_requests)
        self.window = max(1, window)
        self.ban_seconds = max(0, ban_seconds)

    def evaluate(self, timestamps: deque) -> bool:
        now = time.time()
        return sum(1 for t in timestamps if now - t <= self.window) > self.max


class AnomalyDetector:
    """可插拔的异常判定器。

    - ``rules``：异常规则列表（满足任一即判异常）。
    - ``blacklist``：命中后委托其封禁（避免重复封禁）。
    - 内部为每个 IP 维护一个时间戳双端队列（滑动窗口），并在每次 observe 时裁剪过期项。
    """

    def __init__(self, rules, blacklist, lock=None) -> None:
        self.rules = list(rules)
        self.blacklist = blacklist
        self._buckets: dict[str, deque] = defaultdict(deque)
        self._max_window = max((r.window for r in self.rules), default=60)
        self._lock = lock  # 可选线程锁（当前在事件循环内同步调用，默认不加锁也安全）

    def observe(self, ip: str) -> tuple[bool, list[str]]:
        """记录该 IP 的一次请求并判定。

        命中且尚未封禁则执行封禁。返回 ``(is_anomaly, reasons)``：
        ``is_anomaly`` 为 True 表示本次（或之前）已触发异常；``reasons`` 为命中原因列表。
        """
        now = time.time()
        if self._lock is not None:
            self._lock.acquire()
        try:
            dq = self._buckets[ip]
            dq.append(now)
            while dq and now - dq[0] > self._max_window:
                dq.popleft()

            reasons: list[str] = []
            for r in self.rules:
                if r.evaluate(dq):
                    reasons.append(f"{r.name}:>{r.max}/{r.window}s")
                    if not self.blacklist.is_blacklisted(ip):
                        self.blacklist.add(ip, r.ban_seconds)
            return bool(reasons), reasons
        finally:
            if self._lock is not None:
                self._lock.release()

    def reset(self, ip: str | None = None) -> None:
        """清理内存计数（测试 / 运维用）。"""
        if ip is None:
            self._buckets.clear()
        else:
            self._buckets.pop(ip, None)
