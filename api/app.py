"""FastAPI 应用工厂：数据探索 API + 静态资源托管 + 安全防护 + 审计 + 异常判定 + 异步任务。

对外接口与历史版本保持一致（``/api/meta``、``/api/boards``、``/api/board/{name}``、
``/api/jobs`` 等），但内部结构遵循 FastAPI 最佳实践：

- :func:`create_app` 应用工厂 + ``lifespan``：所有单例（配置 / 安全上下文 / 任务管理器 /
  审计存储 / 异常判定器）在工厂内构造并挂到 ``app.state``，便于测试注入与依赖注入取用。
- 路由按资源拆分为 :mod:`api.routers` 下的 ``meta`` / ``boards`` / ``jobs`` / ``graph``，
  统一 ``prefix="/api"`` 与强类型 ``response_model``。
- 配置收敛到 :mod:`api.config` 的 pydantic-settings；安全原语组装在 :mod:`api.security`；
  跨切面依赖在 :mod:`api.deps`。
- **横切中间件独立成公共模块** :mod:`api.middleware`（工厂 ``create_security_audit_middleware``），
  与具体 app 解耦，可被其他 ASGI app 复用；其内顺序为
  **访问规则(如拦截爬虫UA) → 黑名单 → 限流 → 异常判定 → 审计落库**，审计 DB 写入为原生 async
  （``await audit_store.record_audit``），对齐 FastAPI 官方「有异步库就用 async def + await」的 async 文档。
- 限流后端可替换：默认内存 :class:`api.ratelimit.Limiter`，配置 ``GOTY_RATE_LIMIT_REDIS_URL``
  即换 Redis（经 :func:`api.ratelimit.create_rate_limiter` 工厂，调用方无感知）。
- 静态托管：``/`` 永远承载「原始数据页 + 原始洞察页」（v1 只读）；探索 SPA 仅在开启时
  挂到 ``/explore``，需用户主动进入，绝不作为默认落地页。旧 ``/graph`` 书签 307 回根。

运行：``uvicorn api.app:app --host 0.0.0.0 --port 8000``
"""

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import tools  # 触发板块注册（导入即注册）  # noqa: F401
from .anomaly import AnomalyDetector, FrequencyRule
from .audit.store import AuditStore
from .auth import mail as auth_mail
from .auth import pages as auth_pages
from .auth.middleware import create_auth_guard_middleware
from .auth.store import UserStore, create_token_store
from .config import Settings, get_settings
from .deps import get_settings_dep
from .graph_store import get_graph_store
from .logging_config import setup_logging
from .middleware import create_security_audit_middleware
from .routers import admin, auth, boards, graph, jobs, meta
from .rules import BotUserAgentRule
from .security import SecurityContext
from .tasks import TaskManager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLORER_DIR = os.path.join(ROOT, "site", "explorer-graph")
SITE_DIR = os.path.join(ROOT, "site")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        # 回收任务线程池：否则工作线程一直挂着，进程无法正常退出。
        # （任务结果本身的内存回收由 TaskManager 在 create 时按 TTL/上限淘汰。）
        tasks_mgr = getattr(app.state, "tasks_mgr", None)
        if tasks_mgr is not None:
            tasks_mgr.shutdown(wait=False)


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

    # 访问控制规则（默认开启：拦截爬虫/脚本 UA + 空 UA；/api/admin 内部报表豁免）
    access_rules = []
    if settings.block_bot_ua:
        blocked = [s.strip() for s in settings.bot_ua_blocklist.split(",") if s.strip()]
        access_rules.append(
            BotUserAgentRule(
                blocked,
                block_empty_ua=True,
                exempt_prefixes=["/api/admin"],
            )
        )

    # 用户账号 / 会话存储（仅 auth_enabled 时构建；关闭时探索回退匿名流程）
    user_store: UserStore | None = None
    if settings.auth_enabled:
        try:
            user_store = UserStore(settings.users_db_url, echo=settings.users_db_echo)
            user_store.init()
        except Exception:
            log.exception("用户存储初始化失败，认证将不可用（探索页门禁失效）")

    # 邮箱验证令牌存储（可插拔：配置了 Redis 走 Redis，否则走独立 email_tokens 表）+
    # 邮件发送器（off/console/smtp，零第三方依赖）。auth 关闭时令牌存储惰性（None）。
    email_token_store = create_token_store(settings, user_store)
    mail_sender = auth_mail.create_mail_sender(settings)

    # 启动期配置体检：硬策略（验证前禁登录）下，若验证链接不可达或邮件根本发不出去，
    # 新注册用户会**永远无法登录**且日志无任何线索。此处尽早告警，避免线上静默锁死用户。
    if settings.auth_enabled and settings.auth_require_email_verified:
        if not settings.app_public_url:
            log.warning(
                "GOTY_APP_PUBLIC_URL 未配置：验证链接将降级为请求基址，"
                "经反向代理或邮件客户端打开时可能失效，请显式配置公网地址。"
            )
        if (getattr(settings, "mail_mode", "console") or "console").lower() in ("off", "console"):
            log.warning(
                "GOTY_MAIL_MODE=%s 不会发出真实邮件，但 GOTY_AUTH_REQUIRE_EMAIL_VERIFIED=true："
                "新注册用户收不到验证邮件将无法登录。生产请设为 smtp 并配好 SMTP 与发件地址。",
                settings.mail_mode,
            )

    app = FastAPI(
        title="GOTY 知识图谱 · 数据探索 API",
        version="1.12.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.security = security
    app.state.tasks_mgr = tasks_mgr
    app.state.graph_store = graph_store
    app.state.audit_store = audit_store
    app.state.anomaly_detector = anomaly_detector
    app.state.user_store = user_store
    app.state.email_token_store = email_token_store
    app.state.mail_sender = mail_sender

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 安全 + 审计中间件（公共模块工厂，与 app 解耦；详见 api.middleware）
    app.middleware("http")(
        create_security_audit_middleware(
            security, settings, audit_store, anomaly_detector, access_rules, user_store
        )
    )
    # 探索页登录守卫：未登录访问 /explore 跳 /login（仅 auth 开启且需守卫时生效）
    app.middleware("http")(create_auth_guard_middleware(user_store, settings))

    app.include_router(meta.router)
    app.include_router(boards.router)
    app.include_router(jobs.router)
    app.include_router(graph.router)
    app.include_router(admin.router)
    app.include_router(auth.router)

    @app.get("/graph")
    @app.get("/graph/")
    def redirect_graph_to_root():
        """旧书签 /graph/ 直接跳回根（v1 原始页）。"""
        return RedirectResponse(url="/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @app.get("/login")
    def login_page(settings: Settings = Depends(get_settings_dep)):
        """内置登录 / 注册页；认证关闭（全部免登录调试模式）时改为「登录已关闭」提示页。"""
        if not settings.auth_enabled:
            return auth.login_page_disabled()
        return auth.login_page()

    @app.get("/verify-email")
    def verify_email_page():
        """邮箱验证确认页（邮件链接落地页），打开即调验证接口；无需登录。"""
        return auth_pages.verify_email_page()

    # 静态资源：先注册 API 路由，最后按模式挂载静态目录。
    # 无论是否开启探索，根路径 "/" 都默认承载「原始数据页 + 原始洞察页」(v1 只读浏览)；
    # 探索 SPA 仅在开启时挂到 "/explore"，需用户主动进入，绝不作为默认落地页。
    if settings.enable_exploration:

        @app.get("/explore")
        def redirect_explore():
            return RedirectResponse(url="/explore/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

        if os.path.isdir(EXPLORER_DIR):
            app.mount("/explore", StaticFiles(directory=EXPLORER_DIR, html=True), name="explorer")
    if os.path.isdir(SITE_DIR):
        app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="graph")

    return app


# 模块级单例：供 ``uvicorn api.app:app`` 直接加载（生产/本地都以工厂创建）。
app = create_app()
