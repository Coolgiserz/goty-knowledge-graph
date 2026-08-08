"""中间件解耦 + 访问控制(UA) + 限流工厂 测试。

覆盖：中间件已从 api.app 抽离为公共工厂 create_security_audit_middleware；
BotUserAgentRule 拦截爬虫 UA（默认关闭、可开启）；create_rate_limiter 工厂
默认返回内存实现、配置 Redis URL 时给出清晰扩展点。
"""

import pytest
from api.app import create_app
from api.config import Settings
from api.ratelimit import Limiter, RateLimiter, create_rate_limiter
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
    )
    client = TestClient(create_app(settings))
    # 默认关闭，爬虫 UA 也能访问（不影响合法非浏览器客户端 / 测试）
    assert client.get("/api/meta", headers={"user-agent": _bot_ua()}).status_code == 200


def test_rate_limiter_factory_default_in_memory():
    rl = create_rate_limiter(3, 60)
    assert isinstance(rl, Limiter)
    ok, _ = rl.check("k")
    assert ok
    for _ in range(3):
        rl.hit("k")
    ok2, retry = rl.check("k")
    assert not ok2 and retry > 0


def test_rate_limiter_factory_redis_missing_raises():
    # 未安装 redis 时启用 Redis 限流应给出清晰错误（证明扩展点存在且可无缝替换）
    with pytest.raises(RuntimeError):
        create_rate_limiter(3, 60, redis_url="redis://localhost:6379")


def test_security_context_limiters_are_ratelimiter_protocol():
    sc = SecurityContext(Settings(enable_exploration=False))
    assert isinstance(sc.general_limiter, RateLimiter)
    assert isinstance(sc.board_limiter, RateLimiter)
