"""FastAPI 应用工厂：数据探索 API + 静态资源托管 + 安全防护 + 异步任务。

对外接口与历史版本保持一致（``/api/meta``、``/api/boards``、``/api/board/{name}``、
``/api/jobs`` 等），但内部结构遵循 FastAPI 最佳实践：

- :func:`create_app` 应用工厂 + ``lifespan``：所有单例（配置 / 安全上下文 / 任务管理器）
  在工厂内构造并挂到 ``app.state``，便于测试注入与依赖注入取用。
- 路由按资源拆分为 :mod:`api.routers` 下的 ``meta`` / ``boards`` / ``jobs``，
  统一 ``prefix="/api"`` 与强类型 ``response_model``。
- 配置收敛到 :mod:`api.config` 的 pydantic-settings；安全原语组装在
  :mod:`api.security`；跨切面依赖在 :mod:`api.deps`。
- 静态托管：``/`` 永远承载「原始数据页 + 原始洞察页」（v1 只读）；探索 SPA 仅在开启时
  挂到 ``/explore``，需用户主动进入，绝不作为默认落地页。旧 ``/graph`` 书签 307 回根。

运行：``uvicorn api.app:app --host 0.0.0.0 --port 8000``
"""

import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from . import tools  # 触发板块注册（导入即注册）  # noqa: F401
from .config import Settings, get_settings
from .graph_store import get_graph_store
from .logging_config import setup_logging
from .routers import boards, graph, jobs, meta
from .security import SecurityContext
from .tasks import TaskManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLORER_DIR = os.path.join(ROOT, "site", "explorer-graph")
SITE_DIR = os.path.join(ROOT, "site")

log = setup_logging()


class SecurityMiddleware(BaseHTTPMiddleware):
    """黑名单 + 两档限流 + 请求日志。"""

    def __init__(self, app, security: SecurityContext):
        super().__init__(app)
        self.security = security

    async def dispatch(self, request, call_next):
        sec = self.security
        ip = sec.client_ip(request)
        path = request.url.path
        is_board = path.startswith("/api/board/") and request.method == "POST"

        if sec.blacklist.is_blacklisted(ip):
            log.warning("client=%s method=%s path=%s BLOCKED blacklisted", ip, request.method, path)
            return JSONResponse(
                status_code=403,
                content={"error": "blacklisted", "message": "您的访问已被限制，请联系管理员。"},
            )

        ok, retry = sec.general_limiter.check(ip)
        if not ok:
            banned = sec.blacklist.register_violation(
                ip, sec.autoban_violations, sec.autoban_seconds
            )
            log.warning(
                "client=%s method=%s path=%s ratelimit=general retry_after=%d autoban=%s",
                ip,
                request.method,
                path,
                retry,
                banned,
            )
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry)},
                content={
                    "error": "rate_limited",
                    "retry_after": retry,
                    "message": "请求过于频繁，请稍后再试。",
                },
            )

        if is_board:
            ok2, retry2 = sec.board_limiter.check(ip)
            if not ok2:
                banned = sec.blacklist.register_violation(
                    ip, sec.autoban_violations, sec.autoban_seconds
                )
                log.warning(
                    "client=%s method=%s path=%s ratelimit=board retry_after=%d autoban=%s",
                    ip,
                    request.method,
                    path,
                    retry2,
                    banned,
                )
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry2)},
                    content={
                        "error": "rate_limited",
                        "retry_after": retry2,
                        "message": "探索计算请求过于频繁，请稍后再试。",
                    },
                )

        sec.general_limiter.hit(ip)
        if is_board:
            sec.board_limiter.hit(ip)

        start = time.time()
        response = await call_next(request)
        dur_ms = (time.time() - start) * 1000
        log.info(
            "client=%s method=%s path=%s status=%d dur_ms=%.1f",
            ip,
            request.method,
            path,
            response.status_code,
            dur_ms,
        )
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 当前无外部连接池/文件句柄需显式启停；保留钩子以便将来扩展（如预热缓存）。
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂：组装配置、安全上下文、任务管理器、路由与中间件。

    测试可传入自定义的 ``Settings``（如 ``Settings(enable_exploration=True)``）
    来切换模式，无需改动环境变量。
    """
    settings = settings or get_settings()
    security = SecurityContext(settings)
    tasks_mgr = TaskManager(settings.task_workers, settings.max_pending)
    graph_store = get_graph_store(settings)

    app = FastAPI(
        title="GOTY 知识图谱 · 数据探索 API",
        version="1.3.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.security = security
    app.state.tasks_mgr = tasks_mgr
    app.state.graph_store = graph_store

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SecurityMiddleware, security=security)

    app.include_router(meta.router)
    app.include_router(boards.router)
    app.include_router(jobs.router)
    app.include_router(graph.router)

    @app.get("/graph")
    @app.get("/graph/")
    def redirect_graph_to_root():
        """旧书签 /graph/ 直接跳回根（v1 原始页）。"""
        return RedirectResponse(url="/", status_code=307)

    # 静态资源：先注册 API 路由，最后按模式挂载静态目录。
    # 无论是否开启探索，根路径 "/" 都默认承载「原始数据页 + 原始洞察页」(v1 只读浏览)；
    # 探索 SPA 仅在开启时挂到 "/explore"，需用户主动进入，绝不作为默认落地页。
    if settings.enable_exploration:

        @app.get("/explore")
        def redirect_explore():
            return RedirectResponse(url="/explore/", status_code=307)

        if os.path.isdir(EXPLORER_DIR):
            app.mount("/explore", StaticFiles(directory=EXPLORER_DIR, html=True), name="explorer")
    if os.path.isdir(SITE_DIR):
        app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="graph")

    return app


# 模块级单例：供 ``uvicorn api.app:app`` 直接加载（生产/本地都以工厂创建）。
app = create_app()
