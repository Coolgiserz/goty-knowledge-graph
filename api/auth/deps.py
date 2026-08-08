"""FastAPI 依赖：取用户存储、解析当前用户、强制登录门禁。"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from .models import User
from .session import resolve_session_user
from .store import UserStore


def get_user_store(request: Request) -> UserStore | None:
    return request.app.state.user_store


async def get_current_user(request: Request) -> User | None:
    """返回当前登录用户（未登录返回 ``None``），不强制。auth 关闭时恒返回 ``None``。"""
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return None
    store: UserStore | None = request.app.state.user_store
    if store is None:
        return None
    return await resolve_session_user(request, store, settings.session_cookie_name)


async def require_user(request: Request) -> User | None:
    """探索计算/提交接口与 ``/explore`` 页的门禁。

    - ``auth_enabled=True`` 且未登录 -> 401（``authentication_required``）。
    - ``auth_enabled=False`` -> 放行并返回 ``None``（兼容旧匿名流程）。
    """
    settings = request.app.state.settings
    if not settings.auth_enabled:
        return None
    store: UserStore | None = request.app.state.user_store
    user = (
        await resolve_session_user(request, store, settings.session_cookie_name) if store else None
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required"
        )
    return user
