"""认证业务服务层：编排存储层 + 收敛业务规则。

本层是「HTTP 路由（api.routers.auth）」与「存储层（api.auth.store）」之间的业务抽象：

- **存储层** ``api.auth.store`` 只管底层持久化（用户 / 会话的 CRUD、bcrypt 哈希），不承载业务规则。
- **本服务层**承载所有业务规则（字段校验、重名判定、注册开关、会话生命周期），
  是后续迭代（如邮件验证、OAuth、登录限额、审计埋点）的唯一落点，**不动路由与存储**。
- **路由层** ``api.routers.auth`` 只做 HTTP 边界：解析请求体、调用本服务、把
  :class:`AuthError` 翻译成 ``HTTPException``、把会话 id 写成 HttpOnly Cookie（Cookie 属
  HTTP 关注点，留在路由层）。路由**不直接调用存储层**。

错误以 :class:`AuthError` 及其子类表达（携带标准 HTTP 状态码 + 稳定错误码 `detail`），
与前端 ``/login`` 页的中文映射一一对应。
"""

from __future__ import annotations

import re

from .models import User
from .store import UserStore

# ---- 业务规则常量（收敛于此，路由 / 存储均不重复定义）----
# 用户名：3-32 位，字母/数字/._-/，避免空白与特殊字符注入。
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
# 邮箱：基础格式（含 @ 且 @ 前后及域名含点）；非 RFC 全量，足够挡明显错误。
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 密码：至少 8 位，且同时包含字母与数字（基本强度要求）。
PASSWORD_MIN_LEN = 8
PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).+$")


class AuthError(Exception):
    """业务层可预期错误：携带标准 HTTP 状态码与稳定错误码（作响应 ``detail``）。"""

    def __init__(self, status_code: int, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.status_code = status_code
        self.code = code


# ---- 具体业务错误（与前端 /login 页中文映射一一对应）----
class RegistrationClosed(AuthError):
    def __init__(self) -> None:
        super().__init__(403, "registration_closed", "注册已关闭")


class InvalidUsername(AuthError):
    def __init__(self) -> None:
        super().__init__(400, "invalid_username", "用户名格式不正确")


class WeakPassword(AuthError):
    def __init__(self) -> None:
        super().__init__(400, "weak_password", "密码强度不足")


class InvalidEmail(AuthError):
    def __init__(self) -> None:
        super().__init__(400, "invalid_email", "邮箱格式不正确")


class UsernameTaken(AuthError):
    def __init__(self) -> None:
        super().__init__(409, "username_taken", "用户名已被注册")


class AuthStoreUnavailable(AuthError):
    def __init__(self) -> None:
        super().__init__(503, "auth_store_unavailable", "认证服务暂时不可用")


class InvalidCredentials(AuthError):
    def __init__(self) -> None:
        super().__init__(401, "invalid_credentials", "用户名或密码错误")


async def register_user(
    store: UserStore | None,
    username: str,
    password: str,
    email: str,
    *,
    registration_open: bool,
) -> User:
    """注册新用户（业务校验集中在此）。

    成功返回新建 ``User``；任何业务规则不满足抛出对应 :class:`AuthError`，由路由翻译为响应。
    注意：本函数只创建用户，**不负责登录**（会话写入由路由在拿到 user 后调
    :func:`create_session_for` 完成），保持单一职责。
    """
    if not registration_open:
        raise RegistrationClosed()
    if not USERNAME_RE.match(username):
        raise InvalidUsername()
    if not password or len(password) < PASSWORD_MIN_LEN or not PASSWORD_RE.match(password):
        raise WeakPassword()
    if email and not EMAIL_RE.match(email):
        raise InvalidEmail()
    if store is None:
        raise AuthStoreUnavailable()
    try:
        return await store.register(username, password, email)
    except ValueError:
        # 存储层在用户名唯一约束冲突时抛 ValueError（已存在）
        raise UsernameTaken() from None


async def authenticate(
    store: UserStore | None,
    username: str,
    password: str,
) -> User:
    """校验凭据；成功返回用户，失败抛 :class:`InvalidCredentials`。"""
    if store is None:
        raise AuthStoreUnavailable()
    user = await store.authenticate(username, password)
    if user is None:
        raise InvalidCredentials()
    return user


async def create_session_for(
    store: UserStore | None,
    user: User,
    ip: str,
    ttl_seconds: int,
) -> str:
    """为用户创建服务端会话，返回会话 id（Cookie 由路由层写入）。"""
    if store is None:
        raise AuthStoreUnavailable()
    return await store.create_session(user.id, ip, ttl_seconds)


async def delete_session(store: UserStore | None, session_id: str | None) -> None:
    """吊销会话（登出）。``store``/``session_id`` 为空时静默无操作。"""
    if store is not None and session_id:
        await store.delete_session(session_id)
