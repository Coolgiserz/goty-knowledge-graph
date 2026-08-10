"""认证路由（HTTP 边界）：仅负责解析请求、调用业务服务层、组装响应。

分层约定（详见 ``api.auth.service`` / ``api.auth.store`` / ``api.auth.pages``）：

- **本层（路由 / 接口层）**：解析请求体、调用 ``api.auth.service`` 的业务函数、把
  :class:`api.auth.service.AuthError` 翻译成 ``HTTPException``、把会话 id 写成 HttpOnly
  Cookie（Cookie 属 HTTP 关注点，留在此层）。**不直接调用存储层**。
- **业务服务层** ``api.auth.service``：编排存储层 + 所有业务规则。
- **存储层** ``api.auth.store``：底层持久化（用户 / 会话 CRUD、bcrypt 哈希）。
- **页面** ``api.auth.pages``：登录/注册 HTML（与接口代码分离、独立维护）。

路由只暴露：
``POST /api/auth/register`` 自助注册（成功即自动登录）、
``POST /api/auth/login`` 校验并写会话、
``POST /api/auth/logout`` 吊销会话、
``GET  /api/auth/me`` 返回当前登录用户（未登录 401）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..auth import pages, service
from ..auth.deps import get_current_user, get_user_store
from ..auth.models import User
from ..auth.session import clear_session_cookie, set_session_cookie
from ..auth.store import TokenStore, UserStore
from ..config import Settings
from ..deps import get_settings_dep
from ..ratelimit import Limiter, get_client_ip

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 登录/注册页由本模块再导出，保持 api.app 中 `auth.login_page()` 的调用不变。
login_page = pages.login_page
login_page_disabled = pages.login_page_disabled

# 重发验证邮件的频控（内存版，单进程足够；需要跨进程共享时再用 Redis 同构键）。
# 仅做「节流」，命中限流仍返回 200（不泄露账号是否存在），只是跳过本次实际发送。
_resend_limiter = Limiter(max_req=5, window=60)


# ---- HTTP DTO（请求/响应模型，归属路由层）----
class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class RequestVerificationRequest(BaseModel):
    email: str = ""


class VerifyEmailRequest(BaseModel):
    token: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str
    email_verified: bool = False


def _user_out(u: User) -> UserOut:
    return UserOut(
        id=u.id, username=u.username, email=u.email or "", email_verified=bool(u.email_verified)
    )


def _translate(e: service.AuthError) -> HTTPException:
    """把业务错误翻译成 HTTP 异常（稳定 `detail` 错误码供前端映射）。"""
    return HTTPException(status_code=e.status_code, detail=e.code)


def _build_verify_link(settings: Settings, token: str) -> str:
    """构造验证链接：优先用公网基址（容器内 localhost 不可用时由运维指定）。"""
    base = (settings.app_public_url or "").rstrip("/")
    return f"{base}/verify-email?token={token}"


async def _deliver_verification_email(sender, to: str, link: str, ttl: int) -> None:
    """后台发送验证邮件：``asyncio.to_thread`` 把阻塞的 SMTP 调用丢进线程池，不占事件循环。"""
    subject = "请验证你的邮箱 · GOTY 知识图谱"
    body = (
        "感谢注册 GOTY 知识图谱。请点击以下链接完成邮箱验证：\n"
        f"{link}\n\n"
        f"该链接 {ttl // 60} 分钟内有效，且仅可使用一次。若非本人操作，请忽略本邮件。"
    )
    await asyncio.to_thread(sender.send, to, subject, body)


@router.post("/register")
async def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    token_store: TokenStore | None = request.app.state.email_token_store
    mail_sender = request.app.state.mail_sender
    # 业务校验 + 建号（规则在 service 层）；会话写入留给本层（HTTP Cookie 关注点）。
    try:
        user = await service.register_user(
            store,
            req.username,
            req.password,
            req.email,
            registration_open=settings.auth_registration_open,
            email_required=settings.auth_email_required,
        )
    except service.AuthError as e:
        raise _translate(e) from None

    # 硬策略（验证前禁止登录）：注册不自动登录，改发验证邮件，提示用户查收后登录。
    if settings.auth_require_email_verified:
        try:
            token = await service.create_verification_token(
                token_store, user, settings.email_verify_ttl_seconds
            )
            link = _build_verify_link(settings, token)
            background_tasks.add_task(
                _deliver_verification_email,
                mail_sender,
                user.email,
                link,
                settings.email_verify_ttl_seconds,
            )
        except service.AuthError:
            # 令牌存储不可用（auth 关闭等极端情况）：仍建号成功，仅跳过发信。
            pass
        return _user_out(user)

    # 软策略：注册即自动登录（既有行为）。
    ip = request.client.host if request.client else ""
    sid = await service.create_session_for(store, user, ip, settings.session_ttl_seconds)
    set_session_cookie(
        response,
        settings.session_cookie_name,
        sid,
        settings.session_ttl_seconds,
        settings.session_cookie_secure,
    )
    return _user_out(user)


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    try:
        user = await service.authenticate(
            store,
            req.username,
            req.password,
            require_email_verified=settings.auth_require_email_verified,
        )
    except service.AuthError as e:
        raise _translate(e) from None

    ip = request.client.host if request.client else ""
    sid = await service.create_session_for(store, user, ip, settings.session_ttl_seconds)
    set_session_cookie(
        response,
        settings.session_cookie_name,
        sid,
        settings.session_ttl_seconds,
        settings.session_cookie_secure,
    )
    return _user_out(user)


@router.post("/request-verification")
async def request_verification(
    req: RequestVerificationRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    """请求（重发）邮箱验证邮件。

    防枚举：无论邮箱是否存在 / 是否已验证，均返回 200（不泄露账号状态）。频控仅做节流，
    命中限流时跳过本次实际发送，但响应仍是 200。``mail_mode=off``（纯本地调试）时，
    若账号存在且未验证，响应回显令牌便于联调。
    """
    token_store: TokenStore | None = request.app.state.email_token_store
    mail_sender = request.app.state.mail_sender
    # 频控（按客户端 IP）；超限则跳过发送，但仍返回 200（不泄露）。
    ip = get_client_ip(request, getattr(settings, "trust_proxy", True))
    allowed, _ = _resend_limiter.check(ip)
    if not allowed:
        return {"ok": True}

    # auth 关闭 / 存储惰性时，整体无操作（仍 200）。
    if store is None or token_store is None:
        return {"ok": True}

    user = await store.get_by_email(req.email)
    # 账号不存在或已验证：不发送（但仍 200，防枚举）。
    if user is None or user.email_verified:
        return {"ok": True}

    token = await service.create_verification_token(
        token_store, user, settings.email_verify_ttl_seconds
    )
    link = _build_verify_link(settings, token)
    background_tasks.add_task(
        _deliver_verification_email,
        mail_sender,
        user.email,
        link,
        settings.email_verify_ttl_seconds,
    )
    # 纯本地调试（off 模式）回显令牌，免去 SMTP。
    if (getattr(settings, "mail_mode", "console") or "console").lower() == "off":
        return {"ok": True, "token": token}
    return {"ok": True}


@router.post("/verify-email")
async def verify_email(
    req: VerifyEmailRequest,
    request: Request,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    """消费验证令牌，标记邮箱已验证。按令牌判定（不按邮箱），避免账号枚举。"""
    token_store: TokenStore | None = request.app.state.email_token_store
    try:
        user = await service.verify_email(store, token_store, req.token)
    except service.AuthError as e:
        raise _translate(e) from None
    return {"ok": True, "username": user.username}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    sid = request.cookies.get(settings.session_cookie_name)
    await service.delete_session(store, sid)
    clear_session_cookie(response, settings.session_cookie_name)
    return {"ok": True}


@router.get("/me")
async def me(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required"
        )
    return _user_out(current_user)
