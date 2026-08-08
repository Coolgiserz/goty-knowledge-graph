"""测试固件：以应用工厂构造两种模式的 app / 客户端，避免依赖真实环境变量。

- ``client_disabled`` / ``app_disabled``：``enable_exploration=False``（洞察只读模式）。
- ``client_enabled``  / ``app_enabled`` ：``enable_exploration=True``（探索模式）。
- 安全专项 fixture：限流阈值调小、黑名单种子、探索令牌，便于断言防护行为。
"""

import pytest
from api.app import create_app
from api.config import Settings
from fastapi.testclient import TestClient


@pytest.fixture
def settings_disabled() -> Settings:
    return Settings(enable_exploration=False, auth_enabled=False)


@pytest.fixture
def settings_enabled() -> Settings:
    # 不设置令牌：匿名身份即可提交任务（auth 关闭，验证探索逻辑而非登录）
    return Settings(enable_exploration=True, explore_token="", auth_enabled=False)


@pytest.fixture
def app_disabled(settings_disabled):
    return create_app(settings_disabled)


@pytest.fixture
def app_enabled(settings_enabled):
    return create_app(settings_enabled)


@pytest.fixture
def client_disabled(app_disabled):
    return TestClient(app_disabled)


@pytest.fixture
def client_enabled(app_enabled):
    return TestClient(app_enabled)


@pytest.fixture
def settings_ratelimit() -> Settings:
    # 把一般限流阈值调小，便于在单测中触发 429
    return Settings(enable_exploration=False, rate_limit_max=3, rate_window=60, auth_enabled=False)


@pytest.fixture
def client_ratelimit(settings_ratelimit):
    return TestClient(create_app(settings_ratelimit))


@pytest.fixture
def settings_blacklist() -> Settings:
    return Settings(
        enable_exploration=False, blacklist="1.2.3.4", trust_proxy=True, auth_enabled=False
    )


@pytest.fixture
def client_blacklist(settings_blacklist):
    return TestClient(create_app(settings_blacklist))


@pytest.fixture
def settings_token() -> Settings:
    # 开启探索且要求令牌
    return Settings(enable_exploration=True, explore_token="secret-token", auth_enabled=False)


@pytest.fixture
def client_token(settings_token):
    return TestClient(create_app(settings_token))


@pytest.fixture
def settings_auth(tmp_path) -> Settings:
    # 探索开启 + 登录门禁开启，用户库与审计库均存于临时库（隔离）
    return Settings(
        enable_exploration=True,
        explore_token="",
        auth_enabled=True,
        users_db_url=f"sqlite:///{tmp_path}/users.db",
        audit_db_url=f"sqlite:///{tmp_path}/audit.db",
    )


@pytest.fixture
def client_auth(settings_auth):
    return TestClient(create_app(settings_auth))
