"""User-Agent 解析工具。

集中处理两类 UA 相关逻辑，避免散落在中间件：
- :func:`derive_device`：审计时把 UA 推断为设备类别（iOS/Android/Mobile/Desktop/Bot/Unknown）。
- :func:`is_blocked_user_agent` / :data:`DEFAULT_BOT_UA_SUBSTRINGS`：访问规则用来识别
  明显爬虫 / 脚本 UA（如 python / java / go-http），配合 :class:`api.rules.BotUserAgentRule`
  拦截非浏览器流量。
"""

from __future__ import annotations

DEFAULT_BOT_UA_SUBSTRINGS: tuple[str, ...] = (
    "python",
    "java",
    "go-http",  # Go 标准库默认 UA「Go-http-client/1.1」；故意不用裸 "go" 以免误伤 "google*"
    "golang",
    "curl",
    "wget",
    "httpx",
    "requests",
    "scrapy",
    "aiohttp",
    "okhttp",
    "guzzle",
    "node",
    "perl",
    "ruby",
    "php",
    "bot",
    "spider",
    "crawl",
    "slurp",
    "headless",
    "scraper",
    "axios",
    "urllib",
)


def derive_device(ua: str) -> str:
    """从 User-Agent 轻量推断客户端设备类别（无第三方依赖）。"""
    u = (ua or "").lower()
    if not u:
        return "Unknown"
    if "iphone" in u or "ipad" in u or "ipod" in u:
        return "iOS"
    if "android" in u:
        return "Android"
    if "mobile" in u or "windows phone" in u or "blackberry" in u:
        return "Mobile"
    if "bot" in u or "spider" in u or "crawl" in u:
        return "Bot"
    return "Desktop"


def is_blocked_user_agent(ua: str, blocked_substrings: list[str]) -> bool:
    """若 UA 命中任一禁用子串则返回 True（比较已统一为小写）。

    ``blocked_substrings`` 为空时永不匹配；空 UA 默认不拦截（如需「仅浏览器」严格模式，
    由上层规则另行处理）。
    """
    low = (ua or "").lower()
    if not low or not blocked_substrings:
        return False
    return any(s and s in low for s in blocked_substrings)
