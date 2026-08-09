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

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel

from ..auth import pages, service
from ..auth.deps import get_current_user, get_user_store
from ..auth.models import User
from ..auth.session import clear_session_cookie, set_session_cookie
from ..auth.store import UserStore
from ..config import Settings
from ..deps import get_settings_dep

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 登录/注册页由本模块再导出，保持 api.app 中 `auth.login_page()` 的调用不变。
login_page = pages.login_page
login_page_disabled = pages.login_page_disabled


# ---- HTTP DTO（请求/响应模型，归属路由层）----
class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, email=u.email or "")


def _translate(e: service.AuthError) -> HTTPException:
    """把业务错误翻译成 HTTP 异常（稳定 `detail` 错误码供前端映射）。"""
    return HTTPException(status_code=e.status_code, detail=e.code)


@router.post("/register")
async def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    # 业务校验 + 建号（规则在 service 层）；会话写入留给本层（HTTP Cookie 关注点）。
    try:
        user = await service.register_user(
            store,
            req.username,
            req.password,
            req.email,
            registration_open=settings.auth_registration_open,
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


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    try:
        user = await service.authenticate(store, req.username, req.password)
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
