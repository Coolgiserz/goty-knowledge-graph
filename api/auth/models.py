"""认证相关的 SQLAlchemy ORM 模型（独立 Base，用户库只包含用户与会话表）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, false, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class AuthBase(DeclarativeBase):
    pass


class User(AuthBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(default=True)
    # 持久标志：邮箱是否已验证。硬策略下未验证禁止登录。默认值 False（新注册需验证）；
    # 存量账号在迁移时整体标记为已验证（受信任历史账号），详情见 api.auth.store 的 ALTER 迁移。
    # server_default 用 SQLAlchemy 的 false() 而非字符串 "0"：后者会让 PostgreSQL 收到
    # 「整型字面量作 boolean 默认值」而建表失败（SQLite/MySQL 却不报错）。
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SessionRow(AuthBase):
    """服务端会话记录：Cookie 仅持有 ``id``，校验/吊销在数据库完成。"""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(43), primary_key=True)  # secrets.token_urlsafe(32)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    ip: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class EmailToken(AuthBase):
    """邮箱验证 / 密码重置等**临时、单次、短命**令牌（独立表，不污染 ``users``）。

    - 与 ``User.email_verified``（持久状态）分离：令牌用完即焚、过期失效，属临时数据。
    - 默认 DB 后端（零依赖）；若配置了 ``rate_limit_redis_url``，令牌改走 Redis（TTL 自动过期）。
    - ``type`` 预留扩展：``verify``（邮箱验证）/ ``reset``（密码重置，未来复用同一发放/消费逻辑）。
    """

    __tablename__ = "email_tokens"

    token: Mapped[str] = mapped_column(String(43), primary_key=True)  # secrets.token_urlsafe(32)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(16), default="verify")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
