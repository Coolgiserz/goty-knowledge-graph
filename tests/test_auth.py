"""认证模块测试：注册 / 登录 / 登出 / me、探索接口登录门禁、审计记录用户身份、/explore 守卫。

这些测试均显式开启 ``auth_enabled=True`` 并使用临时用户库 / 审计库，与默认关闭登录的
既有 fixture 隔离，不污染只读 / 探索逻辑测试。
"""

import asyncio
import os
import re

import pytest
from api.app import create_app
from api.audit.models import AuditLog
from api.auth.store import DbEmailTokenStore, SyncUserStore, UserStore
from api.config import Settings
from api.registry import run_board as _real_run_board
from api.routers.auth import _resend_limiter
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
        # 基线认证用例固定关闭邮箱验证开关，保持「注册即自动登录、邮箱可选」的旧行为，
        # 使既有回归用例不受新功能影响；邮箱验证流程由下方专用用例（_verify_client）覆盖。
        "auth_email_required": False,
        "auth_require_email_verified": False,
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
    # 已登录分支（直接访问 /login 时展示个人信息 + 退出登录）
    assert "你已登录" in text and "checkLoggedIn" in text and "logout-now" in text


def test_explore_spa_has_user_ui(tmp_path):
    # 探索 SPA 必须内置「退出登录 / 个人信息」入口（解决「没有地方登出/查看信息」）。
    base = os.path.join(os.path.dirname(__file__), "..", "site", "explorer-graph")
    with open(os.path.join(base, "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(base, "app.js"), encoding="utf-8") as f:
        js = f.read()
    assert 'id="logout-btn"' in html
    assert 'id="profile-btn"' in html
    assert "个人信息" in html
    assert "loadUser" in js and "/auth/me" in js
    assert "/auth/logout" in js  # 退出登录调用


def test_login_page_hidden_when_auth_disabled(tmp_path):
    # 全部免登录调试模式：/login 不渲染登录/注册表单
    c = _auth_client(tmp_path, auth_enabled=False)
    r = c.get("/login")
    assert r.status_code == 200
    assert "登录已关闭" in r.text
    assert "doLogin" not in r.text
    assert "doRegister" not in r.text


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


# ---------------------------------------------------------------------------
# 邮箱验证功能（auth_email_required / auth_require_email_verified 开启时）
# ---------------------------------------------------------------------------


class _CapturingMail:
    """测试用邮件发送器：捕获验证链接中的 token，便于在 off/console 模式下断言流程。"""

    def __init__(self) -> None:
        self.tokens: list[str] = []

    def send(self, to: str, subject: str, body: str) -> None:
        m = re.search(r"token=([A-Za-z0-9_-]+)", body)
        if m:
            self.tokens.append(m.group(1))


def _verify_client(tmp_path, **over) -> TestClient:
    """开启邮箱验证开关的客户端（硬策略 + 注册邮箱必填）。"""
    kwargs = {
        "enable_exploration": True,
        "auth_enabled": True,
        "auth_email_required": True,
        "auth_require_email_verified": True,
        "mail_mode": "console",
        "users_db_url": f"sqlite:///{tmp_path}/users.db",
        "audit_db_url": f"sqlite:///{tmp_path}/audit.db",
    }
    kwargs.update(over)
    return TestClient(create_app(Settings(**kwargs)))


def test_register_requires_email(tmp_path):
    # 硬策略前置：邮箱必填，空邮箱 -> 400 email_required
    c = _verify_client(tmp_path)
    r = c.post("/api/auth/register", json={"username": "noemail", "password": "supersecret1"})
    assert r.status_code == 400
    assert r.json()["detail"] == "email_required"


def test_unverified_cannot_login(tmp_path):
    # 硬策略：未验证邮箱禁止登录（401 email_not_verified），且注册不自动登录。
    c = _verify_client(tmp_path)
    reg = c.post(
        "/api/auth/register",
        json={"username": "unverified", "password": "supersecret1", "email": "u@x.com"},
    )
    assert reg.status_code == 200
    assert reg.json()["email_verified"] is False
    # 注册未写会话 Cookie
    assert "goty_session" not in c.cookies
    # 登录被拦截
    r = c.post("/api/auth/login", json={"username": "unverified", "password": "supersecret1"})
    assert r.status_code == 401
    assert r.json()["detail"] == "email_not_verified"


def test_verify_email_full_flow(tmp_path):
    # 注册 -> 验证 -> 登录 全流程（token 经捕获的邮件发送器取得）。
    c = _verify_client(tmp_path)
    cap = _CapturingMail()
    c.app.state.mail_sender = cap
    reg = c.post(
        "/api/auth/register",
        json={"username": "flow", "password": "supersecret1", "email": "flow@x.com"},
    )
    assert reg.status_code == 200
    assert cap.tokens, "注册应触发验证邮件"
    token = cap.tokens[0]
    # 验证前登录仍被拦截
    assert (
        c.post("/api/auth/login", json={"username": "flow", "password": "supersecret1"}).status_code
        == 401
    )
    # 消费令牌
    v = c.post("/api/auth/verify-email", json={"token": token})
    assert v.status_code == 200
    assert v.json()["username"] == "flow"
    # 验证后可登录
    login = c.post("/api/auth/login", json={"username": "flow", "password": "supersecret1"})
    assert login.status_code == 200
    assert login.json()["email_verified"] is True
    # /me 可用
    assert c.get("/api/auth/me").status_code == 200


def test_verify_invalid_or_expired_token(tmp_path):
    c = _verify_client(tmp_path)
    r = c.post("/api/auth/verify-email", json={"token": "nonexistent-token"})
    assert r.status_code == 400
    assert r.json()["detail"] == "invalid_or_expired_token"


def test_verify_already_verified(tmp_path):
    # 已验证后再次消费新令牌 -> 409 already_verified
    # 注：request-verification 接口对已验证用户故意跳过发送（防枚举），故这里直接经 service
    # 为已验证用户再发一枚令牌，验证消费时应返回 already_verified。
    from api.auth import service

    c = _verify_client(tmp_path)
    cap = _CapturingMail()
    c.app.state.mail_sender = cap
    c.post(
        "/api/auth/register",
        json={"username": "twice", "password": "supersecret1", "email": "t@x.com"},
    )
    t1 = cap.tokens[0]
    assert c.post("/api/auth/verify-email", json={"token": t1}).status_code == 200
    # 直接为已验证用户再发放令牌（绕过接口的「已验证则跳过」），消费应得 already_verified
    store = c.app.state.user_store
    token_store = c.app.state.email_token_store
    user = asyncio.run(store.get_by_email("t@x.com"))
    tok = asyncio.run(service.create_verification_token(token_store, user, 3600))
    r = c.post("/api/auth/verify-email", json={"token": tok})
    assert r.status_code == 409
    assert r.json()["detail"] == "already_verified"


def test_resend_idempotent_and_constant_200(tmp_path):
    # 重发：两次均 200；旧令牌被覆盖后失效（消费幂等）。
    _resend_limiter._buckets.clear()  # 隔离频控状态，保证确定性
    c = _verify_client(tmp_path)
    cap = _CapturingMail()
    c.app.state.mail_sender = cap
    c.post(
        "/api/auth/register",
        json={"username": "resend", "password": "supersecret1", "email": "r@x.com"},
    )
    r1 = c.post("/api/auth/request-verification", json={"email": "r@x.com"})
    assert r1.status_code == 200
    old = cap.tokens[-1]
    r2 = c.post("/api/auth/request-verification", json={"email": "r@x.com"})
    assert r2.status_code == 200  # 恒定 200（防枚举）
    new = cap.tokens[-1]
    assert old != new
    # 旧令牌已被覆盖 -> 消费失败
    assert c.post("/api/auth/verify-email", json={"token": old}).status_code == 400
    # 新令牌可成功验证
    assert c.post("/api/auth/verify-email", json={"token": new}).status_code == 200


def test_cli_account_verified_by_default(tmp_path):
    # 同步存储（CLI/脚本建号）默认 email_verified=True，可直接登录。
    store = SyncUserStore(f"sqlite:///{tmp_path}/cli.db")
    store.init()
    u = store.register("cliuser", "supersecret1", "cli@x.com")
    assert u.email_verified is True


def test_db_token_store_consume_idempotent(tmp_path):
    # 直接验证 DbEmailTokenStore：消费一次后即失效；过期令牌返回 None。
    store = UserStore(f"sqlite:///{tmp_path}/tk.db")
    store.init()
    u = asyncio.run(store.register("tkuser", "supersecret1", "tk@x.com", email_verified=False))
    ts = DbEmailTokenStore(store)
    tok = "valid-token-abc"
    asyncio.run(ts.create(u.id, tok, 3600))
    assert asyncio.run(ts.consume(tok)) == u.id  # 首次消费返回 user_id
    assert asyncio.run(ts.consume(tok)) is None  # 已被消费 -> None
    # 过期令牌（ttl 为负 -> 写入即过期）
    expired = "expired-token-xyz"
    asyncio.run(ts.create(u.id, expired, -10))
    assert asyncio.run(ts.consume(expired)) is None
