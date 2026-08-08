"""集中管理 HTTP 状态码与统一错误代码，消除散落各模块的魔法值（magic number / string）。

背景与收益
----------
此前 ``status_code=403/503/404/...`` 与 ``detail="graph_backend_unavailable"`` 这类值在
``middleware.py`` ``app.py`` ``deps.py`` ``routers/*`` 中重复出现（如
``graph_backend_unavailable`` 出现 9 次、``任务不存在`` 出现 4 次），既难全局审查也易改漏。

集中后：
- :class:`HTTP`：HTTP 状态码（``IntEnum``），作为 ``status_code=`` 的具名引用。
- :class:`ErrorCode`：API 统一错误标识字符串（``detail`` / ``error`` 字段值），全局唯一，
  作为前后端契约的一部分；变更文案或码值只需改这里一处。

约定
----
- 错误码值需全局唯一、稳定；对外暴露的用 ASCII snake_case（如 ``graph_backend_unavailable``），
  内部友好的中文提示（如 ``任务不存在``）保持原样但集中管理，避免再出现硬编码散落。
- 复合提示（含动态参数，如 ``未知探索板块: xxx``）将「前缀」抽为常量，沿用 ``f"{ErrorCode.X}: ..."``。
"""

from __future__ import annotations

from enum import IntEnum


class HTTP(IntEnum):
    """项目实际用到的 HTTP 状态码子集（按需补充）。

    ``IntEnum`` 成员即 ``int`` 子类，可直接传给 ``status_code=`` / ``JSONResponse(...)`` 等。
    """

    OK = 200
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    TOO_MANY_REQUESTS = 429
    TEMPORARY_REDIRECT = 307
    SERVICE_UNAVAILABLE = 503


class ErrorCode:
    """API 统一错误标识（``detail`` / ``error`` 字段值）命名空间。

    纯字符串常量类（非 ``StrEnum``）：用普通 ``str`` 语义，序列化 / 比较零意外。
    """

    # ---- 访问控制（中间件硬拦截）----
    BLOCKED = "blocked"
    BLACKLISTED = "blacklisted"
    RATE_LIMITED = "rate_limited"
    BLOCKED_USER_AGENT = "blocked_user_agent"
    BLOCKED_EMPTY_USER_AGENT = "blocked_empty_user_agent"

    # ---- 鉴权 / 令牌 ----
    EXPLORATION_DISABLED = "exploration_disabled"
    INVALID_OR_MISSING_TOKEN = "invalid_or_missing_token"
    UNAUTHORIZED = "unauthorized"
    ADMIN_REPORT_DISABLED = "admin_report_disabled"
    INVALID_ADMIN_TOKEN = "invalid_admin_token"
    AUDIT_STORE_UNAVAILABLE = "audit_store_unavailable"

    # ---- 探索任务 / 板块 ----
    UNKNOWN_BOARD = "未知探索板块"  # 复合提示前缀：f"{ErrorCode.UNKNOWN_BOARD}: {name}"
    TASK_NOT_FOUND = "任务不存在"
    TOO_MANY_PENDING = "too_many_pending"

    # ---- 图查询 ----
    GRAPH_BACKEND_UNAVAILABLE = "graph_backend_unavailable"
    NODE_NOT_FOUND = "node_not_found"
    NO_PATH = "no_path"
