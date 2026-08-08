"""会话 Cookie 助手 + 从请求解析已登录用户。"""

from __future__ import annotations

from typing import Any

from .store import UserStore


def set_session_cookie(response, cookie_name: str, session_id: str, ttl: int, secure: bool) -> None:
    """写 HttpOnly + SameSite=Lax 的会话 Cookie（浏览器自动随同源请求携带）。"""
    response.set_cookie(
        key=cookie_name,
        value=session_id,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response, cookie_name: str) -> None:
    response.delete_cookie(cookie_name, path="/")


async def resolve_session_user(
    request, user_store: UserStore | None, cookie_name: str
) -> Any | None:
    """解析当前已登录用户（返回 ``User`` 或 ``None``）。

    仅在 ``user_store`` 可用时查库；Cookie 缺失 / 会话无效 / 过期均返回 ``None``。
    """
    if user_store is None:
        return None
    sid = request.cookies.get(cookie_name)
    if not sid:
        return None
    return await user_store.get_session_user(sid)
