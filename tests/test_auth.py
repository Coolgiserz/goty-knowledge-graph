"""认证模块测试：注册 / 登录 / 登出 / me、探索接口登录门禁、审计记录用户身份、/explore 守卫。

这些测试均显式开启 ``auth_enabled=True`` 并使用临时用户库 / 审计库，与默认关闭登录的
既有 fixture 隔离，不污染只读 / 探索逻辑测试。
"""

import asyncio

import pytest
from api.app import create_app
from api.audit.models import AuditLog
from api.config import Settings
from api.registry import run_board as _real_run_board
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture
def stub_run_board(monkeypatch):
    """桩替换重计算，聚焦接口层（登录门禁 / owner 归属）。"""

    def fake(name, params, data_matches_baseline):
        return {
            "board": name,
            "params": params,
            "interpretation": "x",
            "validity": {
                "data_matches_baseline": True,
                "interpretation_valid": True,
                "invalid_reasons": [],
            },
            "panels": [],
            "tables": [],
            "metrics": {},
        }

    monkeypatch.setattr("api.registry.run_board", fake)
    yield
    monkeypatch.setattr("api.registry.run_board", _real_run_board)


def _auth_client(tmp_path, **over) -> TestClient:
    kwargs = {
        "enable_exploration": True,
        "explore_token": "",
        "users_db_url": f"sqlite:///{tmp_path}/users.db",
    }
    kwargs.update(over)
    # 默认开启 auth（与既有用例一致）；调用方可用 auth_enabled=False 进入免登录调试模式
    kwargs.setdefault("auth_enabled", True)
    s = Settings(**kwargs)
    return TestClient(create_app(s))


def test_register_auto_logs_in_and_me(client_auth):
    r = client_auth.post(
        "/api/auth/register",
        json={"username": "alice", "password": "supersecret1", "email": "a@x.com"},
    )
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
    # 注册即写入会话 Cookie
    assert "goty_session" in client_auth.cookies
    me = client_auth.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == "alice"


def test_login_wrong_password(client_auth):
    client_auth.post("/api/auth/register", json={"username": "bob", "password": "supersecret1"})
    r = client_auth.post("/api/auth/login", json={"username": "bob", "password": "nope"})
    assert r.status_code == 401


def test_duplicate_username(client_auth):
    client_auth.post("/api/auth/register", json={"username": "carol", "password": "supersecret1"})
    r = client_auth.post("/api/auth/register", json={"username": "carol", "password": "otherpass1"})
    assert r.status_code == 409


def test_weak_password_rejected(client_auth):
    r = client_auth.post("/api/auth/register", json={"username": "dave", "password": "short"})
    assert r.status_code == 400


def test_invalid_username_rejected(client_auth):
    r = client_auth.post("/api/auth/register", json={"username": "a", "password": "supersecret1"})
    assert r.status_code == 400


def test_invalid_email_rejected(client_auth):
    # 邮箱格式错误 -> 400 invalid_email
    r = client_auth.post(
        "/api/auth/register",
        json={"username": "erin", "password": "supersecret1", "email": "not-an-email"},
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_email"


def test_password_requires_letter_and_digit(client_auth):
    # 纯字母（虽 ≥8 位）缺数字 -> 400 weak_password
    r = client_auth.post("/api/auth/register", json={"username": "finn", "password": "allletters"})
    assert r.status_code == 400
    assert r.json()["detail"] == "weak_password"


def test_register_accepts_valid_email(client_auth):
    r = client_auth.post(
        "/api/auth/register",
        json={"username": "gabe", "password": "supersecret1", "email": "gabe@example.com"},
    )
    assert r.status_code == 200
    assert r.json()["email"] == "gabe@example.com"
    me = client_auth.get("/api/auth/me")
    assert me.json()["email"] == "gabe@example.com"


def test_login_page_shows_validation_hints(tmp_path):
    # 认证开启时，/login 页面对用户名的规则、邮箱格式、密码规则给出中文提示，
    # 且包含把服务端错误码翻译为中文的映射（前端预校验体验）。
    c = _auth_client(tmp_path, auth_enabled=True)
    r = c.get("/login")
    assert r.status_code == 200
    text = r.text
    assert "密码至少 8 位，且需同时包含字母和数字" in text
    assert "邮箱格式不正确" in text
    assert "该用户名已被注册" in text  # username_taken 中文映射
    assert "doRegister" in text and "doLogin" in text  # 表单逻辑在位


def test_registration_closed(tmp_path):
    c = _auth_client(tmp_path, auth_registration_open=False)
    r = c.post("/api/auth/register", json={"username": "eve", "password": "supersecret1"})
    assert r.status_code == 403


def test_logout_clears_session(client_auth):
    client_auth.post("/api/auth/register", json={"username": "frank", "password": "supersecret1"})
    assert client_auth.get("/api/auth/me").status_code == 200
    assert client_auth.post("/api/auth/logout").status_code == 200
    assert client_auth.get("/api/auth/me").status_code == 401


def test_jobs_requires_login(client_auth):
    r = client_auth.post("/api/jobs", json={"board": "community", "params": {}})
    assert r.status_code == 401


def test_jobs_allowed_when_logged_in(client_auth, stub_run_board):
    client_auth.post("/api/auth/register", json={"username": "grace", "password": "supersecret1"})
    r = client_auth.post("/api/jobs", json={"board": "community", "params": {}})
    assert r.status_code == 200
    # owner 归属为登录用户名（审计 / 队列可按用户维度追溯）
    assert r.json()["owner"] == "grace"


def test_board_requires_login(client_auth, stub_run_board):
    # 未登录 -> 401
    r = client_auth.post("/api/board/community", json={"params": {}})
    assert r.status_code == 401
    # 登录后 -> 200
    client_auth.post("/api/auth/register", json={"username": "heidi", "password": "supersecret1"})
    r2 = client_auth.post("/api/board/community", json={"params": {}})
    assert r2.status_code == 200


def test_explore_requires_login_redirects(tmp_path):
    c = _auth_client(tmp_path)
    # 未登录访问 /explore -> 跳 /login
    r = c.get("/explore", follow_redirects=False)
    assert r.status_code == 307
    assert "/login" in r.headers["location"]
    # 登录后不再跳转（注意：路由会把 /explore 永久规范化为 /explore/，这是正常的 307，
    # 与「未登录跳 /login」的守卫 307 无关；这里直接访问 /explore/ 验证守卫放行）。
    c.post("/api/auth/register", json={"username": "ivan", "password": "supersecret1"})
    r2 = c.get("/explore/", follow_redirects=False)
    assert "/login" not in r2.headers.get("location", "")


def test_audit_records_username(tmp_path):
    c = _auth_client(
        tmp_path,
        audit_enabled=True,
        audit_db_url=f"sqlite:///{tmp_path}/audit.db",
        audit_log_file="",
    )
    c.post("/api/auth/register", json={"username": "judy", "password": "supersecret1"})
    c.get("/api/meta")  # 携带会话 Cookie -> 审计应记录用户名

    store = c.app.state.audit_store
    assert store is not None

    async def _last_row():
        async with store._session() as s:
            return await s.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))

    row = asyncio.run(_last_row())
    assert row.path == "/api/meta"
    assert row.username == "judy"
    assert row.user_id is not None


def test_password_redacted_in_audit(tmp_path):
    c = _auth_client(
        tmp_path,
        audit_enabled=True,
        audit_db_url=f"sqlite:///{tmp_path}/audit.db",
        audit_log_file="",
    )
    plain = "SUPERSECRET123"
    c.post("/api/auth/register", json={"username": "kate", "password": plain, "email": "k@x.com"})

    store = c.app.state.audit_store
    assert store is not None

    async def _find():
        async with store._session() as s:
            return list(
                await s.scalars(select(AuditLog).where(AuditLog.path == "/api/auth/register"))
            )

    rows = asyncio.run(_find())
    assert rows, "注册请求应写入审计"
    for r in rows:
        body = r.request_body or ""
        assert plain not in body, "明文密码不应进入审计日志（文件/库）"
        assert '"password": "***"' in body, "密码字段应被脱敏为 ***"


def test_login_page_hidden_when_auth_disabled(tmp_path):
    # 全部免登录调试模式：/login 不渲染登录/注册表单
    c = _auth_client(tmp_path, auth_enabled=False)
    r = c.get("/login")
    assert r.status_code == 200
    assert "登录已关闭" in r.text
    assert "doLogin" not in r.text
    assert "doRegister" not in r.text


def test_meta_exposes_auth_enabled(tmp_path):
    assert _auth_client(tmp_path, auth_enabled=True).get("/api/meta").json()["auth_enabled"] is True
    assert (
        _auth_client(tmp_path, auth_enabled=False).get("/api/meta").json()["auth_enabled"] is False
    )


def test_bypass_mode_allows_anonymous(tmp_path, stub_run_board):
    # GOTY_AUTH_ENABLED=false：探索页直接可访问，计算接口免登录直接 200
    c = _auth_client(tmp_path, auth_enabled=False)
    r = c.get("/explore/", follow_redirects=False)
    assert "/login" not in r.headers.get("location", "")
    r2 = c.post("/api/jobs", json={"board": "community", "params": {}})
    assert r2.status_code == 200
