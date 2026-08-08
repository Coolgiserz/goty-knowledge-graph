"""FastAPI 依赖注入层（``Depends``）。

把「配置 / 安全上下文 / 任务管理器 / 探索开关 / 身份解析」收敛为可复用、可单测的
依赖，路由里只声明需要什么，不自己读全局变量。应用工厂把单例挂到 ``app.state``，
依赖通过 ``request.app.state`` 取用，符合 FastAPI 官方推荐模式。
"""

from fastapi import Depends, HTTPException, Request

from .audit.store import AuditStore
from .config import Settings
from .constants import HTTP, ErrorCode
from .graph_store import GraphStore
from .security import SecurityContext
from .tasks import TaskManager


def get_settings_dep(request: Request) -> Settings:
    """返回本应用实例的配置（来自 ``app.state.settings``，即工厂注入或环境变量）。

    不要直接依赖 :func:`api.config.get_settings`：那样会绕过 ``create_app`` 注入的
    测试用配置。通过 ``app.state`` 取用，测试传入自定义 ``Settings`` 即可生效。
    """
    return request.app.state.settings


def get_security(request: Request) -> SecurityContext:
    return request.app.state.security


def get_tasks(request: Request) -> TaskManager:
    return request.app.state.tasks_mgr


def get_graph_store_dep(request: Request) -> GraphStore:
    """返回本应用实例的图存储后端（来自 ``app.state.graph_store``，工厂注入）。"""
    return request.app.state.graph_store


def get_audit_store(request: Request) -> AuditStore | None:
    """返回本应用实例的审计存储（来自 ``app.state.audit_store``，可能为 None）。"""
    return request.app.state.audit_store


def require_exploration(settings: Settings = Depends(get_settings_dep)) -> None:
    """探索总开关守卫：关闭时所有计算/任务接口返回 403。"""
    if not settings.enable_exploration:
        raise HTTPException(status_code=HTTP.FORBIDDEN, detail=ErrorCode.EXPLORATION_DISABLED)


def extract_token(request: Request) -> str:
    """从 Bearer / 自定义头 / query 提取探索令牌（任一即可）。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    t = request.headers.get("x-explore-token")
    if t:
        return t.strip()
    t = request.query_params.get("token")
    return t.strip() if t else ""


def resolve_owner(
    request: Request, settings: Settings, security: SecurityContext
) -> tuple[str | None, str | None]:
    """解析任务归属。返回 ``(owner, err)``，``err`` 非空表示鉴权失败。

    - 配置了 ``explore_token``：必须携带匹配令牌，否则 ``invalid_or_missing_token``。
    - 否则优先用 ``x-user-id`` 头；再退化为按客户端 IP 的匿名身份。
    """
    if settings.explore_token:
        if extract_token(request) != settings.explore_token:
            return None, ErrorCode.INVALID_OR_MISSING_TOKEN
        return "admin", None
    uid = request.headers.get("x-user-id")
    if uid:
        return uid.strip()[:40], None
    return f"anon:{security.client_ip(request)}", None


def is_admin_scope(request: Request, settings: Settings) -> bool:
    """是否持令牌的总览视角（可看全部任务 / 队列全局）。"""
    return bool(settings.explore_token) and extract_token(request) == settings.explore_token
