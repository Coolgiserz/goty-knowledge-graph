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
import secrets

from .models import User
from .store import TokenStore, UserStore

# ---- 业务规则常量（收敛于此，路由 / 存储均不重复定义）----
# 用户名：3-32 位，字母/数字/._-/，避免空白与特殊字符注入。
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
# 邮箱：基础格式（含 @ 且 @ 前后及域名含点）；非 RFC 全量，足够挡明显错误。
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 密码：至少 8 位，且同时包含字母与数字（基本强度要求）。
PASSWORD_MIN_LEN = 8
PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).+$")
# 密码上限：bcrypt 只取前 **72 字节**（超出直接抛 ValueError），必须按字节而非字符计
# ——UTF-8 下中文占 3 字节，约 25 个汉字就会越界。此处提前拒绝，避免用户撞上
# 底层异常（并曾被 ``except ValueError`` 误报成「用户名已存在」，见 register_user）。
PASSWORD_MAX_BYTES = 72


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


class EmailRequired(AuthError):
    def __init__(self) -> None:
        super().__init__(400, "email_required", "请填写邮箱")


class EmailNotVerified(AuthError):
    def __init__(self) -> None:
        super().__init__(401, "email_not_verified", "请先验证邮箱后再登录")


async def register_user(
    store: UserStore | None,
    username: str,
    password: str,
    email: str,
    *,
    registration_open: bool,
    email_required: bool = False,
) -> User:
    """注册新用户（业务校验集中在此）。

    成功返回新建 ``User``（``email_verified=False``）；任何业务规则不满足抛出对应
    :class:`AuthError`，由路由翻译为响应。本函数只创建用户，**不负责登录**（会话写入由路由
    在拿到 user 后调 :func:`create_session_for` 完成），保持单一职责。

    - ``email_required``：开启时邮箱为空 -> :class:`EmailRequired`（硬策略前置条件）。
    - 邮箱非空时的格式校验始终生效（``EmailRequired`` 与 ``InvalidEmail`` 分列，前端可归位）。
    """
    if not registration_open:
        raise RegistrationClosed()
    if not USERNAME_RE.match(username):
        raise InvalidUsername()
    if not password or len(password) < PASSWORD_MIN_LEN or not PASSWORD_RE.match(password):
        raise WeakPassword()
    if len(password.encode("utf-8")) > PASSWORD_MAX_BYTES:
        # bcrypt 上限 72 字节；提前拒绝，避免底层 ValueError 被误判为重名（见下方 except）。
        raise WeakPassword()
    # 邮箱：必填策略下空邮箱直接拒绝；非空时才校验格式。
    if email_required and not email:
        raise EmailRequired()
    if email and not EMAIL_RE.match(email):
        raise InvalidEmail()
    if store is None:
        raise AuthStoreUnavailable()
    try:
        return await store.register(username, password, email, email_verified=False)
    except ValueError as e:
        # 存储层仅在用户名唯一约束冲突时抛 ``ValueError("username_taken")``。
        # 其余 ValueError（如 bcrypt 参数越界）若一并吞掉会伪装成「用户名已存在」，
        # 误导用户反复改用户名却始终注册失败，故此处只转换重名一种情形。
        if str(e) != "username_taken":
            raise
        raise UsernameTaken() from None


async def authenticate(
    store: UserStore | None,
    username: str,
    password: str,
    *,
    require_email_verified: bool = False,
) -> User:
    """校验凭据；成功返回用户，失败抛 :class:`InvalidCredentials`。

    ``require_email_verified``：硬策略开启时，邮箱未验证直接抛 :class:`EmailNotVerified`
    （凭据本身正确，只是被邮箱验证门禁拦截），由路由翻译为 ``401``。
    """
    if store is None:
        raise AuthStoreUnavailable()
    user = await store.authenticate(username, password)
    if user is None:
        raise InvalidCredentials()
    if require_email_verified and not user.email_verified:
        raise EmailNotVerified()
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


async def create_verification_token(
    token_store: TokenStore | None,
    user: User,
    ttl: int,
) -> str:
    """为指定用户生成并存储邮箱验证令牌，返回令牌字符串。

    令牌为 32 字节 URL-safe 随机值（与 ``SessionRow.id`` 同源），单次有效、短 TTL；
    覆盖旧令牌使重发幂等。``token_store`` 为空（auth 关闭 / 惰性）时抛
    :class:`AuthStoreUnavailable`。
    """
    if token_store is None:
        raise AuthStoreUnavailable()
    token = secrets.token_urlsafe(32)
    await token_store.create(user.id, token, ttl)
    return token


async def verify_email(
    store: UserStore | None,
    token_store: TokenStore | None,
    token: str,
) -> User:
    """消费验证令牌并把用户标记为已验证，返回用户。

    - 令牌不存在 / 过期 -> ``AuthError(400, "invalid_or_expired_token")``。
    - 令牌有效但邮箱已验证（重复点击）-> ``AuthError(409, "already_verified")``。
    - 成功：``store.mark_verified`` 置 ``email_verified=True`` 后返回用户。
    """
    if token_store is None or store is None:
        raise AuthStoreUnavailable()
    user_id = await token_store.consume(token)  # 原子：校验未过期 + 取 user_id + 删行
    if user_id is None:
        raise AuthError(400, "invalid_or_expired_token", "验证链接无效或已过期")
    user = await store.get_user(user_id)
    if user is None:
        raise AuthError(400, "invalid_or_expired_token", "验证链接无效或已过期")
    if user.email_verified:
        raise AuthError(409, "already_verified", "该邮箱已验证")
    await store.mark_verified(user.id)
    return user
