#!/usr/bin/env python3
"""站点访问统计报表 CLI（内部运维用，不面向用户）。

读取审计库（默认 ``GOTY_AUDIT_DB_URL``，即 ``sqlite:///./data/audit.db``），聚合生成人读报表。

用法：
    python scripts/audit_report.py
    python scripts/audit_report.py --from 2026-08-01 --to 2026-08-31 --group-by day
    python scripts/audit_report.py --group-by month --include-bots
    python scripts/audit_report.py --db-url sqlite:////path/to/audit.db

说明：
- 此脚本直连审计库，无需 ``GOTY_ADMIN_TOKEN``（令牌仅用于 HTTP 接口鉴权）。
- 依赖 ``uv run`` 环境；仓库根目录下运行即可（脚本会自动把根目录加入 sys.path）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime

# 允许以脚本方式直接运行：把仓库根目录加入 sys.path 以便 ``import api``。
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from api.audit.report import build_report  # noqa: E402
from api.audit.store import AuditStore  # noqa: E402
from api.config import Settings  # noqa: E402


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        print(f"[忽略] 无法解析时间参数: {value!r}", file=sys.stderr)
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _print_report(rep: dict) -> None:
    win = rep["window"]
    tot = rep["totals"]
    line = "=" * 64
    dash = "-" * 64
    print(line)
    print("站点访问统计报表（内部）")
    print(f"生成时间 : {rep['generated_at']}")
    print(
        f"时间窗   : {win['from']} ~ {win['to']}  "
        f"(分组: {win['group_by']}, 排除爬虫: {win['exclude_bots']})"
    )
    print(dash)
    print(f"总请求数 : {tot['total_requests']}")
    print(f"页面浏览 : {tot['page_views']}  (PV)")
    print(f"接口调用 : {tot['api_calls']}")
    print(f"独立访客 : {tot['unique_visitors']}  (UV, 按 ip+UA 指纹去重)")
    print(dash)
    print("按时间分布:")
    for row in rep["visits_over_time"]:
        print(f"  {row['bucket']:<16} PV={row['page_views']:<6} API={row['api_calls']:<6} UV={row['visitors']}")
    if rep["top_pages"]:
        print(dash)
        print("Top 页面:")
        for row in rep["top_pages"]:
            print(f"  {row['views']:<6} {row['path']}")
    if rep["top_apis"]:
        print(dash)
        print("Top 接口:")
        for row in rep["top_apis"]:
            print(f"  {row['calls']:<6} {row['path']}")
    print(dash)
    print("设备分布:")
    for dev, n in rep["device_breakdown"].items():
        print(f"  {dev:<10} {n}")
    print(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="GOTY 站点访问统计报表（内部运维）")
    parser.add_argument("--db-url", default=None, help="审计库 SQLAlchemy URL（默认读 GOTY_AUDIT_DB_URL）")
    parser.add_argument("--from", dest="from_", default=None, help="起始时间 ISO8601，如 2026-08-01")
    parser.add_argument("--to", dest="to_", default=None, help="结束时间 ISO8601，如 2026-08-31")
    parser.add_argument("--group-by", choices=("day", "hour", "month"), default="day")
    parser.add_argument("--include-bots", action="store_true", help="不排除爬虫/异常流量")
    args = parser.parse_args()

    settings = Settings()
    db_url = args.db_url or settings.audit_db_url
    store = AuditStore(db_url)
    store.init()  # 同步入口（CLI 无运行中的事件循环）

    rep = asyncio.run(
        build_report(
            store,
            from_=_parse_dt(args.from_),
            to=_parse_dt(args.to_),
            group_by=args.group_by,
            exclude_bots=not args.include_bots,
        )
    )
    _print_report(rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
