"""中间件解耦 + 访问控制(UA) + 限流工厂 测试。

覆盖：中间件已从 api.app 抽离为公共工厂 create_security_audit_middleware；
BotUserAgentRule 拦截爬虫 UA（默认关闭、可开启）；create_rate_limiter 工厂
默认返回内存实现、配置 Redis URL 时给出清晰扩展点。
"""

import asyncio

import pytest
from api.app import create_app
from api.config import Settings
from api.ratelimit import Blacklist, Limiter, RateLimiter, create_rate_limiter
from api.security import SecurityContext
from fastapi.testclient import TestClient


def _browser_ua() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )


def _bot_ua() -> str:
    return "python-requests/2.31.0"


def test_middleware_blocks_bot_user_agent():
    settings = Settings(
        enable_exploration=False,
        block_bot_ua=True,
        audit_enabled=False,
        anomaly_enabled=False,
        auth_enabled=False,
    )
    client = TestClient(create_app(settings))
    # 爬虫 UA -> 403
    assert client.get("/api/meta", headers={"user-agent": _bot_ua()}).status_code == 403
    # 真实浏览器 UA -> 200
    assert client.get("/api/meta", headers={"user-agent": _browser_ua()}).status_code == 200
    # 浏览器 UA 访问其他接口也不受影响
    assert client.get("/api/boards", headers={"user-agent": _browser_ua()}).status_code == 200


def test_middleware_allows_bot_ua_when_disabled():
    settings = Settings(
        enable_exploration=False,
        block_bot_ua=False,
        audit_enabled=False,
        anomaly_enabled=False,
        auth_enabled=False,
    )
    client = TestClient(create_app(settings))
    # 默认关闭，爬虫 UA 也能访问（不影响合法非浏览器客户端 / 测试）
    assert client.get("/api/meta", headers={"user-agent": _bot_ua()}).status_code == 200


def test_rate_limiter_factory_default_in_memory():
    # 限流器协议为 async（中间件是 async 的，Redis 后端必须用 async 客户端才不阻塞循环）
    rl = create_rate_limiter(3, 60)
    assert isinstance(rl, Limiter)
    assert asyncio.run(rl.check("k"))[0]
    for _ in range(3):
        asyncio.run(rl.hit("k"))
    ok2, retry = asyncio.run(rl.check("k"))
    assert not ok2 and retry > 0


def test_rate_limiter_factory_redis_missing_raises():
    # 未安装 redis 时启用 Redis 限流应给出清晰错误（证明扩展点存在且可无缝替换）
    with pytest.raises(RuntimeError):
        create_rate_limiter(3, 60, redis_url="redis://localhost:6379")


def test_security_context_limiters_are_ratelimiter_protocol():
    sc = SecurityContext(Settings(enable_exploration=False))
    assert isinstance(sc.general_limiter, RateLimiter)
    assert isinstance(sc.board_limiter, RateLimiter)


def test_limiter_buckets_are_bounded():
    """限流桶必须有界：_buckets 曾永不清理，IP 维度（可伪造）持续灌新键即无界增长。"""
    lim = Limiter(2, 60, max_keys=100)

    async def _flood():
        for i in range(300):  # 远超上限的键数
            await lim.check_and_hit(f"ip-{i}")
        return len(lim._buckets)

    n = asyncio.run(_flood())
    assert n <= 100, f"桶数量应有界（<=100），实际 {n}"


def test_blacklist_clears_violations_after_ban():
    """封禁后必须清零违规计数，否则临时封禁过期后「一次即封」，等于永久封禁。"""
    bl = Blacklist(seed=[])
    banned = False
    for _ in range(3):
        banned = bl.register_violation("1.2.3.4", 3, 60)
    assert banned is True
    assert bl.violations.get("1.2.3.4", 0) == 0, "封禁后违规计数应清零"
    assert bl.is_blacklisted("1.2.3.4") is True


def test_healthcheck_style_ua_blocked_but_browser_ua_allowed(tmp_path):
    """urllib 默认 UA（Python-urllib）会被 Bot 规则拦截 -> Docker 健康检查必须显式带浏览器 UA。

    回归背景：Dockerfile 的 HEALTHCHECK 用 urllib.request.urlopen，其默认 UA 含
    "python"/"urllib"，而 bot_ua_blocklist 默认开启且含这两项 -> 健康检查恒 403，
    容器被误判 unhealthy。
    """
    settings = Settings(
        enable_exploration=False,
        auth_enabled=False,
        block_bot_ua=True,
        users_db_url=f"sqlite:///{tmp_path}/u.db",
        audit_db_url=f"sqlite:///{tmp_path}/a.db",
    )
    client = TestClient(create_app(settings))
    assert client.get("/api/meta", headers={"user-agent": "Python-urllib/3.12"}).status_code == 403
    # 健康检查改用浏览器 UA 后应放行
    ua = "Mozilla/5.0 (healthcheck) GOTY-HealthCheck"
    assert client.get("/api/meta", headers={"user-agent": ua}).status_code == 200
