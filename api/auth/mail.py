"""邮件发送器：可插拔、零第三方依赖（标准库 ``smtplib`` / ``email``）。

设计取向（详见 ``docs/EMAIL_VERIFICATION.md`` 第 2.4 / 5 节）：

- 本项目此前无任何 SMTP 配置；若强制真实邮件发送，本地开发 / demo 将无法联调。因此抽一个
  :class:`MailSender` 协议，由 ``GOTY_MAIL_MODE`` 在 ``off`` / ``console`` / ``smtp`` 间切换。
- **零依赖**：真实发送只用 Python 标准库 ``smtplib`` + ``email.message.EmailMessage``，
  不引入任何第三方包（规避 v1.8.1 漏导锁文件致容器缺包的覆辙）。
- ``off``：``send`` 直接 no-op（纯本地调试；令牌改由接口回显，见路由层）。
- ``console``：把验证链接打印到应用日志（零外部服务，最轻联调）。
- ``smtp``：经标准库发真实邮件；本地测试可指向 **MailHog**（``localhost:1025``，无鉴权、无 TLS），
  Web UI 在 ``localhost:8025`` 检视（见 ``docs/EMAIL_VERIFICATION.md`` 第 13.1 节）。
- 未来接 SendGrid / SES 只需再加一个实现，路由层零改动。
"""

from __future__ import annotations

import logging
from typing import Protocol

from ..config import Settings

logger = logging.getLogger("goty.auth.mail")


class MailSender(Protocol):
    """邮件发送协议：同步阻塞发送（调用方负责用 ``asyncio.to_thread`` 丢进线程池）。"""

    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailSender:
    """仅打印到日志（零外部服务），便于无 SMTP 的本地 / demo 联调。"""

    def send(self, to: str, subject: str, body: str) -> None:
        logger.info("【验证码邮件(console)】to=%s\nsubject=%s\n%s", to, subject, body)


class NullMailSender:
    """``mail_mode=off`` 时使用：什么都不做（纯本地调试，令牌由接口回显）。"""

    def send(self, to: str, subject: str, body: str) -> None:
        logger.debug("mail_mode=off，跳过邮件发送（to=%s）", to)


class SmtpMailSender:
    """标准库 SMTP 发送（无新依赖）。MailHog 等无鉴权 / 无 TLS 的本地 SMTP 直接可用。"""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        sender: str,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.sender = sender or "no-reply@goty.local"

    def send(self, to: str, subject: str, body: str) -> None:
        from email.message import EmailMessage
        from smtplib import SMTP

        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        # 标准库同步阻塞调用；MailHog 无鉴权、无 TLS 可直接连通。生产 SMTP 如需 TLS，
        # 可在此处按需启用 starttls——本期配置表未含 TLS 开关，保持零配置本地体验。
        with SMTP(self.host, self.port, timeout=10) as smtp:
            if self.user:
                smtp.login(self.user, self.password)
            smtp.send_message(msg)


def create_mail_sender(settings: Settings) -> MailSender:
    """按 ``GOTY_MAIL_MODE`` 返回对应发送器实现。"""
    mode = (getattr(settings, "mail_mode", "console") or "console").lower()
    if mode == "off":
        return NullMailSender()
    if mode == "smtp":
        return SmtpMailSender(
            host=getattr(settings, "smtp_host", "localhost") or "localhost",
            port=int(getattr(settings, "smtp_port", 1025) or 1025),
            user=getattr(settings, "smtp_user", "") or "",
            password=getattr(settings, "smtp_password", "") or "",
            sender=getattr(settings, "mail_from", "") or "",
        )
    # console 为默认（零依赖、最安全）
    return ConsoleMailSender()
