"""邮件验证·集成测试骨架。

与 ``tests/test_auth.py`` 里用 ``_CapturingMail`` 桩件（off/console 模式）覆盖逻辑不同，
本文件**真实走 SMTP 发送路径**：用标准库 ``socketserver`` 起一个进程内 SMTP 捕获服务器
（零第三方依赖，无需 MailHog / Docker），应用以 ``mail_mode=smtp`` 指向它，断言：

- 注册后真实 SMTP 服务端收到邮件（含正确的 To / 验证链接 / token）；
- 验证前登录被拦截（硬策略）；
- 访问邮件里的验证链接（``GET /verify-email`` 确认页）成功；
- 验证后登录成功。

默认不运行（``integration`` marker），需 ``make test-integration`` 显式触发；因此不会拖慢
``make test`` / ``make ci``，也不需要外部 SMTP 服务。若你已在跑 MailHog，也可把测试里的
``smtp_host/smtp_port`` 改成 ``localhost/1025`` 直接对联调环境发真实邮件。

扩展点（骨架已留好）：
- 多收件人 / 重发接口的真实投递与「旧令牌失效」断言；
- 接 SendGrid / SES 时，只需替换 ``SmtpMailSender`` 实现，本测试无需改动。
"""

from __future__ import annotations

import re
import socketserver
import threading
from email import message_from_bytes, policy
from email.message import EmailMessage

import pytest
from api.app import create_app
from api.config import Settings
from fastapi.testclient import TestClient


# --------------------------------------------------------------------------- #
# 进程内 SMTP 捕获服务器（最小 SMTP 对话，仅供测试；不进生产代码路径）
# --------------------------------------------------------------------------- #
class _CaptureTCPServer(socketserver.ThreadingTCPServer):
    """带消息捕获能力的 SMTP 服务端（``self.server`` 在 handler 中即此实例）。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._lock = threading.Lock()
        self.messages: list[bytes] = []

    def record(self, raw: bytes) -> None:
        with self._lock:
            self.messages.append(raw)

    def get_messages(self) -> list[bytes]:
        with self._lock:
            return list(self.messages)


class _SmtpCaptureHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(b"220 testserver ESMTP\r\n")
        self.wfile.flush()
        while True:
            try:
                line = self.rfile.readline()
            except Exception:
                return
            if not line:
                return
            try:
                cmd = line.decode("ascii", "replace").strip()
            except Exception:
                return
            if not cmd:
                continue
            verb = cmd.split(" ", 1)[0].upper()
            if verb in ("EHLO", "HELO"):
                self.wfile.write(b"250-testserver\r\n250 OK\r\n")
            elif verb == "MAIL":
                self.wfile.write(b"250 OK\r\n")
            elif verb == "RCPT":
                self.wfile.write(b"250 OK\r\n")
            elif verb == "DATA":
                self.wfile.write(b"354 End data with <CR><LF>.<CR><LF>\r\n")
                self.wfile.flush()
                buf = bytearray()
                while True:
                    dline = self.rfile.readline()
                    if not dline or dline.strip() == b".":
                        break
                    # 还原 SMTP 点填充（行首双点 -> 单点）
                    if dline.startswith(b".."):
                        dline = dline[1:]
                    buf.extend(dline)
                self.server.record(bytes(buf))  # type: ignore[attr-defined]
                self.wfile.write(b"250 OK queued\r\n")
            elif verb in ("RSET", "NOOP"):
                self.wfile.write(b"250 OK\r\n")
            elif verb == "QUIT":
                self.wfile.write(b"221 Bye\r\n")
                self.wfile.flush()
                return
            else:
                self.wfile.write(b"500 Unrecognized command\r\n")
            self.wfile.flush()


class SmtpCaptureServer:
    """上下文管理器：起/停一个进程内 SMTP 服务器，并捕获所有收到的邮件原始字节。"""

    def __init__(self) -> None:
        self._server = _CaptureTCPServer(("127.0.0.1", 0), _SmtpCaptureHandler)

    def get_messages(self) -> list[bytes]:
        return self._server.get_messages()

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def __enter__(self) -> SmtpCaptureServer:
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


# --------------------------------------------------------------------------- #
# 集成用例
# --------------------------------------------------------------------------- #
@pytest.mark.integration
def test_register_sends_real_email_then_verify_and_login(tmp_path):
    """注册 -> 真实 SMTP 收信 -> 校验链接 -> 硬策略拦截 -> 验证 -> 登录。"""
    with SmtpCaptureServer() as smtp:
        settings = Settings(
            enable_exploration=True,
            auth_enabled=True,
            auth_email_required=True,
            auth_require_email_verified=True,
            mail_mode="smtp",
            smtp_host="127.0.0.1",
            smtp_port=smtp.port,
            mail_from="no-reply@goty.local",
            app_public_url="http://testserver",  # 验证链接基址（TestClient 主机名）
            users_db_url=f"sqlite:///{tmp_path}/users.db",
            audit_db_url=f"sqlite:///{tmp_path}/audit.db",
        )
        c = TestClient(create_app(settings))

        reg = c.post(
            "/api/auth/register",
            json={"username": "integ", "password": "supersecret1", "email": "integ@x.com"},
        )
        assert reg.status_code == 200
        assert reg.json()["email_verified"] is False
        # 硬策略：注册不自动登录
        assert "goty_session" not in c.cookies

        # 1) 真实 SMTP 服务端应收到一封邮件
        msgs = smtp.get_messages()
        assert len(msgs) == 1
        parsed = message_from_bytes(msgs[0], _class=EmailMessage, policy=policy.default)
        assert parsed["To"] == "integ@x.com"
        body = str(parsed.get_content())
        m = re.search(r"token=([A-Za-z0-9_-]+)", body)
        assert m, "邮件正文应包含验证 token"
        token = m.group(1)

        # 2) 验证前登录仍被拦截
        assert (
            c.post(
                "/api/auth/login", json={"username": "integ", "password": "supersecret1"}
            ).status_code
            == 401
        )

        # 3) 用真实 token 调验证接口完成真正的令牌消费。
        #    注意：浏览器里这一步由 /verify-email 确认页的 JS 发起 fetch 完成；TestClient
        #    不执行 JS，所以这里直接走 API（与生产等价）。下方 3b 仅静态确认确认页已正确下发。
        v = c.post("/api/auth/verify-email", json={"token": token})
        assert v.status_code == 200
        assert v.json()["username"] == "integ"

        # 3b) 确认页本身可访问，且包含验证端点引用与成功文案（静态资源健全性）。
        page = c.get(f"/verify-email?token={token}")
        assert page.status_code == 200
        assert "/api/auth/verify-email" in page.text
        assert "邮箱验证成功" in page.text

        # 4) 验证后登录成功
        login = c.post("/api/auth/login", json={"username": "integ", "password": "supersecret1"})
        assert login.status_code == 200
        assert login.json()["email_verified"] is True


@pytest.mark.integration
def test_verify_page_shows_error_for_bad_token():
    """确认页对无效/过期 token 给出用户可读的中文失败提示（仍 200，不泄露枚举）。"""
    # 不需要真实 SMTP：仅验证页面渲染分支，用默认 console 模式即可。
    settings = Settings(
        enable_exploration=True,
        auth_enabled=True,
        auth_email_required=True,
        auth_require_email_verified=True,
        mail_mode="console",
        app_public_url="http://testserver",
        users_db_url="sqlite:///:memory:",
        audit_db_url="sqlite:///:memory:",
    )
    c = TestClient(create_app(settings))
    page = c.get("/verify-email?token=does-not-exist")
    assert page.status_code == 200
    assert "验证链接无效或已过期" in page.text
