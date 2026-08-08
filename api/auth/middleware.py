"""探索页登录守卫中间件：未登录访问 ``/explore`` 跳转到 ``/login``。

与 :mod:`api.middleware` 的「安全 + 审计」中间件正交——本守卫只负责探索页的登录态，
且只拦截导航（页面），不影响 ``/api/*`` 数据接口（计算/提交接口由各路由自身的
``require_user`` 依赖门禁）。auth 关闭或无需守卫时整体透传。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import status
from fastapi.responses import RedirectResponse

from .session import resolve_session_user


def create_auth_guard_middleware(user_store, settings):
    """返回 ASGI 函数中间件。auth 关闭或无需守卫时直接透传。"""
    if not (settings.auth_enabled and settings.explore_requires_auth):

        async def passthrough(request, call_next):
            return await call_next(request)

        return passthrough

    cookie_name = settings.session_cookie_name
    login_path = "/login"

    async def guard(request, call_next):
        path = request.url.path
        # 登录页与认证 API 永远放行（否则会死循环）
        if path == login_path or path.startswith("/api/auth/"):
            return await call_next(request)
        # 探索页需要登录
        if path.startswith("/explore"):
            user = await resolve_session_user(request, user_store, cookie_name)
            if user is None:
                target = f"{login_path}?next={quote(path)}"
                return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
        return await call_next(request)

    return guard
