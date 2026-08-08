"""站点访问统计测试：PV 埋点 + 访客指纹 + 报告聚合 + 内部管理接口鉴权。

不面向用户的内部统计功能，覆盖：
- 中间件按响应 Content-Type 判定 route_type（page/api/asset）并据此审计/计 PV；
- 访客指纹 ``sha256(ip|UA)[:16]`` 的稳定性与区分度；
- :func:`api.audit.report.build_report` 的 PV/UV/设备分布聚合；
- 内部接口 ``GET /api/admin/report`` 的令牌鉴权（禁用/401/200）。
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from api.app import create_app
from api.audit.models import AuditLog
from api.audit.report import build_report
from api.audit.store import AuditStore
from api.config import Settings
from api.middleware import _classify_route, _compute_visitor_id
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select


def _make_app(tmp_path, **overrides) -> FastAPI:
    db = tmp_path / "audit.db"
    settings = Settings(
        enable_exploration=False,
        audit_enabled=True,
        audit_db_url=f"sqlite:///{db}",
        audit_log_file="",
        anomaly_enabled=False,
        **overrides,
    )
    return create_app(settings)


# ---------------------------------------------------------------------------
# 路由分类 + 访客指纹（纯单元）
# ---------------------------------------------------------------------------


def test_classify_route():
    assert _classify_route("/api/meta", "application/json") == "api"
    assert _classify_route("/api/board/goty", "application/json") == "api"
    assert _classify_route("/", "text/html; charset=utf-8") == "page"
    assert _classify_route("/index.html", "text/html") == "page"
    assert _classify_route("/static/style.css", "text/css") == "asset"
    assert _classify_route("/static/app.js", "application/javascript") == "asset"
    assert _classify_route("/static/logo.png", "image/png") == "asset"
    assert _classify_route("/static/font.woff2", "font/woff2") == "asset"


def test_compute_visitor_id_stable_and_distinct():
    a = _compute_visitor_id("1.2.3.4", "Mozilla/5.0")
    b = _compute_visitor_id("1.2.3.4", "Mozilla/5.0")
    c = _compute_visitor_id("1.2.3.4", "OtherUA")
    d = _compute_visitor_id("9.9.9.9", "Mozilla/5.0")
    assert a == b  # 同 ip+UA 稳定
    assert a != c  # 不同 UA 区分
    assert a != d  # 不同 IP 区分
    assert len(a) == 16  # sha256 前 16 位 hex


# ---------------------------------------------------------------------------
# 中间件埋点：page + api 入库，asset 跳过；指纹/来源写入
# ---------------------------------------------------------------------------


def test_middleware_records_page_and_api_and_skips_asset(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)
    # 首页（site/ 挂载后是 text/html） -> page；接口 -> api
    client.get("/")
    client.get("/api/meta")
    store = app.state.audit_store
    assert store is not None

    async def _rows():
        async with store._session() as s:
            return (await s.execute(select(AuditLog).order_by(AuditLog.id))).scalars().all()

    rows = asyncio.run(_rows())
    types = {r.route_type for r in rows}
    assert "page" in types
    assert "api" in types
    page_rows = [r for r in rows if r.route_type == "page"]
    assert page_rows, "应有至少一条 page 记录"
    # 指纹稳定且已填充；直接访问无 referer
    assert page_rows[0].visitor_id and len(page_rows[0].visitor_id) == 16
    assert page_rows[0].referer == ""


def test_middleware_visitor_id_changes_with_ua(tmp_path):
    app = _make_app(tmp_path)
    client = TestClient(app)
    client.get("/", headers={"User-Agent": "Mozilla/5.0 (PC)"})
    client.get("/", headers={"User-Agent": "Mozilla/5.0 (Phone)"})
    store = app.state.audit_store

    async def _visitors():
        async with store._session() as s:
            res = await s.execute(select(AuditLog.visitor_id).where(AuditLog.route_type == "page"))
            return [r[0] for r in res.all()]

    visitors = asyncio.run(_visitors())
    # 不同 UA -> 不同指纹（同一 IP）
    assert len(set(visitors)) == 2


# ---------------------------------------------------------------------------
# 报告聚合
# ---------------------------------------------------------------------------


def _audit_dict(path, route_type, visitor_id, ip="1.2.3.4", ts=None, device="Desktop"):
    d = {
        "request_id": uuid.uuid4().hex,
        "client_ip": ip,
        "client_device": device,
        "user_agent": "ua",
        "method": "GET",
        "path": path,
        "query": "",
        "request_body": "",
        "status_code": 200,
        "duration_ms": 1.0,
        "is_anomaly": False,
        "anomaly_reasons": "",
        "response_snippet": "",
        "visitor_id": visitor_id,
        "referer": "",
        "route_type": route_type,
    }
    if ts is not None:
        d["ts"] = ts
    return d


async def _seed(store: AuditStore) -> None:
    await store._ensure_schema()
    now = datetime.now(UTC)
    rows = [
        _audit_dict("/", "page", "v1", ts=now - timedelta(days=2)),
        _audit_dict("/", "page", "v1", ts=now - timedelta(days=2)),  # 同访客，UV 不增
        _audit_dict("/insight", "page", "v2", ts=now - timedelta(days=1)),
        _audit_dict("/api/meta", "api", "v1", ts=now - timedelta(days=1)),
        _audit_dict("/api/board/goty", "api", "v3", ts=now - timedelta(days=1), device="Bot"),
    ]
    for r in rows:
        await store.record_audit(r)


def test_report_aggregates_page_views_and_uv(tmp_path):
    db = tmp_path / "audit.db"
    store = AuditStore(f"sqlite:///{db}")
    asyncio.run(_seed(store))

    # exclude_bots=True：剔除 device=Bot 的 api 行（uv3 被排除）
    rep = asyncio.run(build_report(store, exclude_bots=True))
    assert rep["totals"]["page_views"] == 3
    assert rep["totals"]["api_calls"] == 1  # v3(Bot) 被排除
    assert rep["totals"]["unique_visitors"] == 2  # v1, v2
    assert rep["totals"]["total_requests"] == 4

    # exclude_bots=False：包含 Bot 行
    rep2 = asyncio.run(build_report(store, exclude_bots=False))
    assert rep2["totals"]["api_calls"] == 2
    assert rep2["totals"]["unique_visitors"] == 3  # v1, v2, v3

    # Top 页面与设备分布
    assert rep["top_pages"][0]["path"] == "/"
    assert rep["top_pages"][0]["views"] == 2
    assert "Desktop" in rep["device_breakdown"]


def test_report_time_window_filters(tmp_path):
    db = tmp_path / "audit.db"
    store = AuditStore(f"sqlite:///{db}")
    asyncio.run(_seed(store))

    now = datetime.now(UTC)
    # 窗口起点设为 25 小时前：剔除 2 天前的两条 page，保留 1 天前的 /insight 与 api 行
    rep = asyncio.run(build_report(store, from_=now - timedelta(hours=25), exclude_bots=True))
    assert rep["totals"]["page_views"] == 1  # 仅 /insight
    assert rep["totals"]["api_calls"] == 1


def test_report_visits_over_time_buckets(tmp_path):
    db = tmp_path / "audit.db"
    store = AuditStore(f"sqlite:///{db}")
    asyncio.run(_seed(store))
    rep = asyncio.run(build_report(store, group_by="day", exclude_bots=True))
    # 三条不同日期的 page/api 至少产生 2 个时间桶
    buckets = {b["bucket"] for b in rep["visits_over_time"]}
    assert len(buckets) >= 2
    # 每个桶的 visitors 为非负整数
    for b in rep["visits_over_time"]:
        assert isinstance(b["visitors"], int) and b["visitors"] >= 0


# ---------------------------------------------------------------------------
# 内部管理接口鉴权
# ---------------------------------------------------------------------------


def test_admin_report_disabled_without_token(tmp_path):
    # 显式 admin_token="" 覆盖本地 .env 可能存在的 GOTY_ADMIN_TOKEN，确保「未配置即禁用」
    app = _make_app(tmp_path, admin_token="")
    client = TestClient(app)
    assert client.get("/api/admin/report").status_code == 403


def test_admin_report_requires_valid_token(tmp_path):
    app = _make_app(tmp_path, admin_token="secret")
    client = TestClient(app)
    # 无令牌 -> 401
    assert client.get("/api/admin/report").status_code == 401
    # 错误令牌 -> 401
    assert (
        client.get("/api/admin/report", headers={"Authorization": "Bearer wrong"}).status_code
        == 401
    )
    # Bearer 正确 -> 200
    r = client.get("/api/admin/report", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert "totals" in r.json()
    # query 参数正确 -> 200
    assert client.get("/api/admin/report?token=secret").status_code == 200


def test_admin_report_aggregates_through_endpoint(tmp_path):
    app = _make_app(tmp_path, admin_token="secret")
    client = TestClient(app)
    # 先制造一些访问
    client.get("/")
    client.get("/api/meta")
    r = client.get("/api/admin/report?token=secret&exclude_bots=true")
    assert r.status_code == 200
    body = r.json()
    assert body["totals"]["page_views"] >= 1
    assert body["totals"]["api_calls"] >= 1
    assert body["totals"]["unique_visitors"] >= 1


# ---------------------------------------------------------------------------
# UA 拦截（默认开启）：爬虫/脚本 UA + 空 UA 拒拦；浏览器放行；/api/admin 豁免
# ---------------------------------------------------------------------------


def test_bot_user_agent_is_blocked(tmp_path):
    app = _make_app(tmp_path, block_bot_ua=True)
    client = TestClient(app)
    r = client.get("/api/meta", headers={"User-Agent": "python-requests/2.31"})
    assert r.status_code == 403
    assert r.json()["error"] == "blocked"  # 由 UA 规则拦截，而非接口禁用


def test_empty_user_agent_is_blocked(tmp_path):
    app = _make_app(tmp_path, block_bot_ua=True)
    client = TestClient(app)
    r = client.get("/api/meta", headers={"User-Agent": ""})
    assert r.status_code == 403
    assert r.json()["error"] == "blocked"


def test_browser_user_agent_is_allowed(tmp_path):
    app = _make_app(tmp_path, block_bot_ua=True)
    client = TestClient(app)
    r = client.get(
        "/api/meta",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
    )
    assert r.status_code == 200


def test_admin_endpoint_exempt_from_ua_block(tmp_path):
    # /api/admin 前缀豁免 UA 拦截：即使带爬虫 UA，也应到达令牌守卫而非被 UA 规则 403。
    app = _make_app(tmp_path, block_bot_ua=True, admin_token="secret")
    client = TestClient(app)
    r = client.get("/api/admin/report", headers={"User-Agent": "python-requests/2.31"})
    # 未被 UA 拦截（否则 error=="blocked" 的 403），到达令牌守卫：无令牌 -> 401
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid_admin_token"


def test_ua_block_disabled_when_flag_off(tmp_path):
    # 关闭 block_bot_ua 后，爬虫 UA 不再被拦截（服务间 API 调用场景）。
    app = _make_app(tmp_path, block_bot_ua=False)
    client = TestClient(app)
    r = client.get("/api/meta", headers={"User-Agent": "python-requests/2.31"})
    assert r.status_code == 200
