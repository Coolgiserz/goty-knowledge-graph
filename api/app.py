"""FastAPI 应用工厂：数据探索 API + 静态资源托管 + 安全防护 + 审计 + 异常判定 + 异步任务。

对外接口与历史版本保持一致（``/api/meta``、``/api/boards``、``/api/board/{name}``、
``/api/jobs`` 等），但内部结构遵循 FastAPI 最佳实践：

- :func:`create_app` 应用工厂 + ``lifespan``：所有单例（配置 / 安全上下文 / 任务管理器 /
  审计存储 / 异常判定器）在工厂内构造并挂到 ``app.state``，便于测试注入与依赖注入取用。
- 路由按资源拆分为 :mod:`api.routers` 下的 ``meta`` / ``boards`` / ``jobs`` / ``graph``，
  统一 ``prefix="/api"`` 与强类型 ``response_model``。
- 配置收敛到 :mod:`api.config` 的 pydantic-settings；安全原语组装在 :mod:`api.security`；
  跨切面依赖在 :mod:`api.deps`。
- 单入口中间件（原生 ``@app.middleware("http")`` 函数中间件，而非 ``BaseHTTPMiddleware``）：
  负责 **黑名单 → 限流 → 异常判定 → 审计落库**，且审计的数据库写入走 ``asyncio.to_thread``，
  不阻塞事件循环（对齐 FastAPI 官方 async 文档：阻塞 I/O 不应留在主循环）。
- 静态托管：``/`` 永远承载「原始数据页 + 原始洞察页」（v1 只读）；探索 SPA 仅在开启时
  挂到 ``/explore``，需用户主动进入，绝不作为默认落地页。旧 ``/graph`` 书签 307 回根。

运行：``uvicorn api.app:app --host 0.0.0.0 --port 8000``
"""

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import tools  # 触发板块注册（导入即注册）  # noqa: F401
from .anomaly import AnomalyDetector, FrequencyRule
from .audit.store import AuditStore
from .config import Settings, get_settings
from .graph_store import get_graph_store
from .logging_config import log_audit_event, setup_logging
from .routers import boards, graph, jobs, meta
from .security import SecurityContext
from .tasks import TaskManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLORER_DIR = os.path.join(ROOT, "site", "explorer-graph")
SITE_DIR = os.path.join(ROOT, "site")


def derive_device(ua: str) -> str:
    """从 User-Agent 轻量推断客户端设备类别（无第三方依赖）。"""
    u = (ua or "").lower()
    if not u:
        return "Unknown"
    if "iphone" in u or "ipad" in u or "ipod" in u:
        return "iOS"
    if "android" in u:
        return "Android"
    if "mobile" in u or "windows phone" in u or "blackberry" in u:
        return "Mobile"
    if "bot" in u or "spider" in u or "crawl" in u:
        return "Bot"
    return "Desktop"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 当前无外部连接池/文件句柄需显式启停；保留钩子以便将来扩展（如预热缓存）。
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂：组装配置、安全上下文、任务管理器、审计存储、异常判定器、路由与中间件。

    测试可传入自定义的 ``Settings``（如 ``Settings(enable_exploration=True)``）
    来切换模式，无需改动环境变量。
    """
    settings = settings or get_settings()
    log = setup_logging(settings)
    security = SecurityContext(settings)
    tasks_mgr = TaskManager(settings.task_workers, settings.max_pending)
    graph_store = get_graph_store(settings)

    # 审计存储（SQLAlchemy；sqlite 打通流程，未来换 mysql/OLAP 仅改 GOTY_AUDIT_DB_URL）
    audit_store: AuditStore | None = None
    if settings.audit_enabled:
        try:
            audit_store = AuditStore(settings.audit_db_url, echo=settings.audit_db_echo)
            audit_store.init()
        except Exception:
            log.exception("审计存储初始化失败，审计入库将禁用（仍有文件日志）")

    # 异常判定（可插拔规则；默认频率规则 -> 命中拉黑 anomaly_ban_seconds）
    anomaly_detector: AnomalyDetector | None = None
    if settings.anomaly_enabled:
        anomaly_detector = AnomalyDetector(
            [
                FrequencyRule(
                    settings.anomaly_frequency_max,
                    settings.anomaly_frequency_window,
                    settings.anomaly_ban_seconds,
                )
            ],
            security.blacklist,
        )

    app = FastAPI(
        title="GOTY 知识图谱 · 数据探索 API",
        version="1.4.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.security = security
    app.state.tasks_mgr = tasks_mgr
    app.state.graph_store = graph_store
    app.state.audit_store = audit_store
    app.state.anomaly_detector = anomaly_detector

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def security_audit_middleware(request: Request, call_next):
        """单入口安全 + 审计中间件（原生函数中间件，对齐 FastAPI async 最佳实践）。

        顺序：黑名单 → 限流（一般 / 板块）→ 异常判定 → 处理请求 → 审计落库。
        审计的数据库写入经 ``asyncio.to_thread``，不阻塞事件循环。
        """
        sec = request.app.state.security
        st = request.app.state.settings
        astore = getattr(request.app.state, "audit_store", None)
        detector = getattr(request.app.state, "anomaly_detector", None)

        ip = sec.client_ip(request)
        path = request.url.path
        method = request.method
        is_api = path.startswith("/api/")
        is_board = path.startswith("/api/board/") and method == "POST"
        audit_enabled = getattr(st, "audit_enabled", False)
        anomaly_enabled = getattr(st, "anomaly_enabled", False)

        response = None
        anomaly_reasons: list[str] = []
        req_body = ""
        dur_ms = 0.0

        # 1) 黑名单
        if sec.blacklist.is_blacklisted(ip):
            log.warning("client=%s method=%s path=%s BLOCKED blacklisted", ip, method, path)
            response = JSONResponse(
                status_code=403,
                content={"error": "blacklisted", "message": "您的访问已被限制，请联系管理员。"},
            )
        else:
            # 2) 一般限流
            ok, retry = sec.general_limiter.check(ip)
            if not ok:
                banned = sec.blacklist.register_violation(
                    ip, sec.autoban_violations, sec.autoban_seconds
                )
                log.warning(
                    "client=%s method=%s path=%s ratelimit=general retry_after=%d autoban=%s",
                    ip,
                    method,
                    path,
                    retry,
                    banned,
                )
                response = JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry)},
                    content={
                        "error": "rate_limited",
                        "retry_after": retry,
                        "message": "请求过于频繁，请稍后再试。",
                    },
                )
            # 3) 板块级限流（仅探索计算 POST /api/board/*）
            elif is_board:
                ok2, retry2 = sec.board_limiter.check(ip)
                if not ok2:
                    banned = sec.blacklist.register_violation(
                        ip, sec.autoban_violations, sec.autoban_seconds
                    )
                    log.warning(
                        "client=%s method=%s path=%s ratelimit=board retry_after=%d autoban=%s",
                        ip,
                        method,
                        path,
                        retry2,
                        banned,
                    )
                    response = JSONResponse(
                        status_code=429,
                        headers={"Retry-After": str(retry2)},
                        content={
                            "error": "rate_limited",
                            "retry_after": retry2,
                            "message": "探索计算请求过于频繁，请稍后再试。",
                        },
                    )

        if response is None:
            # 4) 异常判定（频率规则）：对每个未被拉黑的 api 请求计数
            if anomaly_enabled and detector and is_api:
                hit, reasons = detector.observe(ip)
                if hit:
                    anomaly_reasons = reasons
                    log.warning(
                        "client=%s method=%s path=%s ANOMALY freq ban ip for %ds",
                        ip,
                        method,
                        path,
                        st.anomaly_ban_seconds,
                    )
                    if astore:
                        await asyncio.to_thread(
                            astore.record_anomaly,
                            {
                                "request_id": uuid.uuid4().hex,
                                "client_ip": ip,
                                "rule": "frequency",
                                "detail": ";".join(reasons),
                                "action": f"blacklist_{st.anomaly_ban_seconds}s",
                            },
                        )
            # 5) 计数（通过后）
            sec.general_limiter.hit(ip)
            if is_board:
                sec.board_limiter.hit(ip)

            # 6) 读取请求体用于审计（仅写操作；Starlette 会缓存 body，下游仍可正常解析）
            if is_api and method in ("POST", "PUT", "PATCH"):
                try:
                    raw = await request.body()
                    if raw:
                        req_body = raw[: st.audit_body_max_bytes].decode("utf-8", "replace")
                except Exception:
                    req_body = ""

            # 7) 处理请求
            start = time.time()
            response = await call_next(request)
            dur_ms = (time.time() - start) * 1000
        # else: 被拦截（黑名单 / 限流）——仍纳入审计，但无响应体 / 耗时

        # 8) 审计日志：文件（时间轮转，同步、轻量）+ 数据库（SQLAlchemy，to_thread 不阻塞）
        if audit_enabled and is_api:
            status = response.status_code
            snippet = ""
            try:
                body = getattr(response, "body", None)
                if body and len(body) <= st.audit_body_max_bytes:
                    snippet = body.decode("utf-8", "replace")
            except Exception:
                snippet = ""
            record = {
                "request_id": uuid.uuid4().hex,
                "client_ip": ip,
                "client_device": derive_device(request.headers.get("user-agent", "")),
                "user_agent": request.headers.get("user-agent", ""),
                "method": method,
                "path": path,
                "query": request.url.query,
                "request_body": req_body,
                "status_code": status,
                "duration_ms": round(dur_ms, 2),
                "is_anomaly": bool(anomaly_reasons),
                "anomaly_reasons": ";".join(anomaly_reasons),
                "response_snippet": snippet,
            }
            log_audit_event(record)
            if astore:
                await asyncio.to_thread(astore.record_audit, record)

        return response

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
