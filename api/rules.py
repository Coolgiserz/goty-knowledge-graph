"""可插拔的访问控制规则（请求级硬拦截）。

与 :mod:`api.anomaly` 的「频率异常→拉黑」不同，这里的规则是**即时访问裁决**：
命中即返回 403，不写入黑名单（除非规则自行决定）。

所有规则实现 :class:`AccessRule` 协议（``name`` + ``should_block(request)``），
中间件按顺序依次询问，任一命中即拦截。新增策略（如地域封禁、UA 指纹库）只需实现协议并
注册到中间件工厂，主流程零改动。
"""

from __future__ import annotations

from typing import Protocol

from fastapi import Request

from .ua import is_blocked_user_agent


class AccessRule(Protocol):
    """访问规则协议：给定请求，判定是否应被拦截。"""

    name: str

    def should_block(self, request: Request) -> tuple[bool, str]:
        """返回 ``(block, reason)``；``block=True`` 时 ``reason`` 为拦截原因标识。"""
        ...


class BotUserAgentRule:
    """拦截明显爬虫 / 脚本 User-Agent（如 python / java / go-http）。

    仅做子串黑名单匹配，零依赖、O(1) 开销。开启 ``block_bot_ua`` 后由中间件工厂装配，
    用于「只放行真实浏览器」的部署场景。

    - ``block_empty_ua``（默认 True）：空 UA（扫描器 / 恶意工具常见特征）也拦截。
    - ``exempt_prefixes``：命中这些路径前缀的请求**不**受 UA 拦截（如 ``/api/admin``
      内部报表接口，由令牌守卫独立鉴权，避免运维 curl / 脚本被误伤）。
    """

    name = "bot_user_agent"

    def __init__(
        self,
        blocked_substrings: list[str] | None = None,
        *,
        block_empty_ua: bool = True,
        exempt_prefixes: list[str] | None = None,
    ) -> None:
        self.blocked = [s.lower() for s in (blocked_substrings or [])]
        self.block_empty_ua = block_empty_ua
        self.exempt_prefixes = list(exempt_prefixes or [])

    def should_block(self, request: Request) -> tuple[bool, str]:
        path = request.url.path
        # 豁免路径（如运维内部报表）不走 UA 拦截，令牌守卫随后生效
        if any(path.startswith(p) for p in self.exempt_prefixes):
            return False, ""
        ua = request.headers.get("user-agent", "") or ""
        if not ua and self.block_empty_ua:
            return True, "blocked_empty_user_agent"
        if is_blocked_user_agent(ua, self.blocked):
            return True, "blocked_user_agent"
        return False, ""
