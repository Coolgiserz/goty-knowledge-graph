"""内部管理路由：站点访问统计报表（**不面向用户开放**）。

仅暴露 ``GET /api/admin/report``，需 ``GOTY_ADMIN_TOKEN`` 鉴权；未配置令牌时整体返回 403，
避免误暴露。访客统计口径见 :mod:`api.audit.report`。
"""

from __future__ import annotations

import hmac
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from ..audit.report import build_report
from ..audit.store import AuditStore
from ..config import Settings
from ..constants import ErrorCode
from ..deps import get_audit_store, get_settings_dep

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _extract_admin_token(request: Request) -> str:
    """从 ``Authorization: Bearer <token>`` 或 ``?token=`` 提取管理令牌（任一即可）。"""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    t = request.query_params.get("token")
    return t.strip() if t else ""


def _require_admin(request: Request, settings: Settings = Depends(get_settings_dep)) -> None:
    """管理接口守卫：未配置令牌返回 403；令牌不匹配返回 401（常量时间比较防时序侧信道）。"""
    token = settings.admin_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=ErrorCode.ADMIN_REPORT_DISABLED
        )
    provided = _extract_admin_token(request)
    if not provided or not hmac.compare_digest(provided, token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=ErrorCode.INVALID_ADMIN_TOKEN
        )


def _parse_dt(value: str | None) -> datetime | None:
    """宽松解析 ISO8601（支持尾随 ``Z`` 与无时区），失败返回 ``None``。"""
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


@router.get("/report")
async def report(
    request: Request,
    _: None = Depends(_require_admin),
    store: AuditStore | None = Depends(get_audit_store),
    from_: str | None = Query(default=None, alias="from", description="起始时间 ISO8601"),
    to: str | None = Query(default=None, alias="to", description="结束时间 ISO8601"),
    group_by: str = Query(default="day", pattern="^(day|hour|month)$"),
    exclude_bots: bool = Query(default=True, description="剔除爬虫/异常流量"),
) -> dict[str, Any]:
    """站点访问统计报表（内部接口，需 ``GOTY_ADMIN_TOKEN`` 鉴权）。"""
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=ErrorCode.AUDIT_STORE_UNAVAILABLE,
        )
    return await build_report(
        store,
        from_=_parse_dt(from_),
        to=_parse_dt(to),
        group_by=group_by,
        exclude_bots=exclude_bots,
    )
