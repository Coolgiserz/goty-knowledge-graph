"""站点访问统计报告：基于审计表（``AuditLog``）聚合 PV / UV / 活跃访客 / 设备分布等。

设计要点：
- **不面向用户开放**，仅经内部接口（``GET /api/admin/report``，``GOTY_ADMIN_TOKEN`` 鉴权）
  与运维 CLI（``scripts/audit_report.py``）使用，了解站点活跃度。
- 聚合在 **Python 层**完成：``AuditStore`` 取出时间窗内的原始行后做计数/去重，跨数据库驱动
  稳健（不依赖 SQLite/PG 专有 SQL），对小体量 GOTY 站点足够。
- 访客标识 ``visitor_id`` = ``sha256(ip|UA)[:16]``（中间件计算、不下发 Cookie）；UV 去重据此。
- ``exclude_bots``：剔除 ``is_anomaly`` 命中与 ``client_device == 'Bot'`` 的行，得到人类访客口径；
  关闭时包含爬虫/异常流量。
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from .models import AuditLog
from .store import AuditStore


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bucket_key(ts: datetime, group_by: str) -> str:
    """把时间戳归入时间序列分桶键（日/时/月），缺失时区补 UTC。"""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    if group_by == "hour":
        return ts.strftime("%Y-%m-%d %H:00")
    if group_by == "month":
        return ts.strftime("%Y-%m")
    return ts.strftime("%Y-%m-%d")  # day


def _route_type_of(rt: str | None) -> str:
    """迁移前旧行 ``route_type`` 为 NULL，旧行为皆为 api 请求，归并为 ``api``。"""
    return rt or "api"


async def _fetch_rows(
    store: AuditStore,
    from_: datetime | None,
    to: datetime | None,
    exclude_bots: bool,
) -> list[tuple]:
    """取出时间窗内的统计所需原始行（仅投影必要列，降低内存占用）。"""
    async with store._session() as s:
        stmt = select(
            AuditLog.ts,
            AuditLog.path,
            AuditLog.route_type,
            AuditLog.visitor_id,
            AuditLog.client_device,
            AuditLog.user_agent,
            AuditLog.is_anomaly,
        )
        if from_ is not None:
            stmt = stmt.where(AuditLog.ts >= from_)
        if to is not None:
            stmt = stmt.where(AuditLog.ts <= to)
        if exclude_bots:
            stmt = stmt.where(AuditLog.is_anomaly == False)  # noqa: E712
            stmt = stmt.where(AuditLog.client_device != "Bot")
        result = await s.execute(stmt)
        return list(result.all())


async def build_report(
    store: AuditStore,
    *,
    from_: datetime | None = None,
    to: datetime | None = None,
    group_by: str = "day",
    exclude_bots: bool = True,
) -> dict[str, Any]:
    """聚合访问统计，返回 JSON 安全的报告字典（供接口/CLI 直接序列化）。

    参数：
    - ``from_`` / ``to``：时间窗（带时区的 datetime；``None`` = 不限）。
    - ``group_by``：时间序列分组粒度（``day`` / ``hour`` / ``month``）。
    - ``exclude_bots``：剔除异常/爬虫流量，得到人类访客口径（默认开）。
    """
    rows = await _fetch_rows(store, from_, to, exclude_bots)

    page_views = 0
    api_calls = 0
    total_requests = 0
    unique_visitors: set[str] = set()
    top_pages: Counter = Counter()
    top_apis: Counter = Counter()
    device_counter: Counter = Counter()
    over_time: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"page_views": 0, "api_calls": 0, "visitors": set()}
    )

    for ts, path, route_type, visitor_id, device, _ua, _is_anomaly in rows:
        rt = _route_type_of(route_type)
        total_requests += 1
        device_counter[device or "Unknown"] += 1
        if rt == "page":
            page_views += 1
            top_pages[path] += 1
        elif rt == "api":
            api_calls += 1
            top_apis[path] += 1
        # asset 行不计入 PV/API（理论上审计库已不含 asset，这里兜底跳过）。
        if visitor_id:
            unique_visitors.add(visitor_id)
        bucket = _bucket_key(ts, group_by)
        b = over_time[bucket]
        if rt == "page":
            b["page_views"] += 1
        elif rt == "api":
            b["api_calls"] += 1
        if visitor_id:
            b["visitors"].add(visitor_id)

    visits_over_time = [
        {
            "bucket": b,
            "page_views": v["page_views"],
            "api_calls": v["api_calls"],
            "visitors": len(v["visitors"]),
        }
        for b, v in sorted(over_time.items())
    ]

    per_bucket_unique = sum(len(v["visitors"]) for v in over_time.values())

    return {
        "generated_at": _now_iso(),
        "window": {
            "from": from_.isoformat(timespec="seconds") if from_ else None,
            "to": to.isoformat(timespec="seconds") if to else None,
            "group_by": group_by,
            "exclude_bots": exclude_bots,
        },
        "totals": {
            "total_requests": total_requests,
            "page_views": page_views,
            "api_calls": api_calls,
            "unique_visitors": len(unique_visitors),
        },
        "visits_over_time": visits_over_time,
        "top_pages": [{"path": p, "views": c} for p, c in top_pages.most_common(10)],
        "top_apis": [{"path": p, "calls": c} for p, c in top_apis.most_common(10)],
        "device_breakdown": dict(device_counter.most_common()),
        "active_visitors": {
            "unique_in_window": len(unique_visitors),
            "per_bucket_avg": (round(per_bucket_unique / len(over_time), 2) if over_time else 0),
        },
    }
