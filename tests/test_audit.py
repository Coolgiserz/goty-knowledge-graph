"""请求审计日志 + 异常判定测试。

覆盖：AuditStore（SQLAlchemy/sqlite 落库）、FrequencyRule/AnomalyDetector（频率超限拉黑）、
中间件审计落库、异常频率命中后后续请求被 403。
"""

import asyncio
import time
import uuid
from collections import deque

from api.anomaly import AnomalyDetector, FrequencyRule
from api.app import create_app
from api.audit.models import AuditLog
from api.audit.store import AuditStore, SyncAuditStore, create_audit_store
from api.config import Settings
from api.ratelimit import Blacklist
from fastapi.testclient import TestClient
from sqlalchemy import select


def _sample_audit(client_ip: str = "1.2.3.4") -> dict:
    return {
        "request_id": uuid.uuid4().hex,
        "client_ip": client_ip,
        "client_device": "Desktop",
        "user_agent": "test-agent",
        "method": "GET",
        "path": "/api/meta",
        "query": "",
        "request_body": "",
        "status_code": 200,
        "duration_ms": 1.5,
        "is_anomaly": False,
        "anomaly_reasons": "",
        "response_snippet": "{}",
    }


def _sample_anomaly(client_ip: str = "1.2.3.4") -> dict:
    return {
        "request_id": uuid.uuid4().hex,
        "client_ip": client_ip,
        "rule": "frequency",
        "detail": "frequency:>3/60s",
        "action": "blacklist_86400s",
    }


# 异步接口（FastAPI 中间件使用）：在事件循环内直接 await，无需 asyncio.run 包裹。
async def test_audit_store_async_sqlite_records(tmp_path):
    db = tmp_path / "audit.db"
    store = AuditStore(f"sqlite:///{db}")
    # init() 在运行中循环内会被跳过，首次写入经 _ensure_schema 惰性建表
    assert await store.count_audit() == 0
    await store.record_audit(_sample_audit())
    assert await store.count_audit() == 1
    assert await store.count_audit("1.2.3.4") == 1
    assert await store.count_audit("9.9.9.9") == 0
    await store.record_anomaly(_sample_anomaly())
    assert await store.count_anomalies("1.2.3.4") == 1


# 同步接口（运维脚本 / 同步测试使用）：纯同步，无 await、无事件循环。
def test_audit_store_sync_sqlite_records(tmp_path):
    db = tmp_path / "audit.db"
    store = SyncAuditStore(f"sqlite:///{db}")
    store.init()
    assert store.count_audit() == 0
    store.record_audit(_sample_audit())
    assert store.count_audit() == 1
    store.record_anomaly(_sample_anomaly())
    assert store.count_anomalies("1.2.3.4") == 1


def test_create_audit_store_selects_impl(tmp_path):
    db = tmp_path / "audit.db"
    url = f"sqlite:///{db}"
    assert isinstance(create_audit_store(url, async_=True), AuditStore)
    assert isinstance(create_audit_store(url, async_=False), SyncAuditStore)


def test_frequency_rule_evaluate():
    r = FrequencyRule(max_requests=3, window=60, ban_seconds=86400)
    now = time.time()
    dq = deque([now - 10, now - 5, now - 1])  # 3 次在窗内 -> 未超过
    assert r.evaluate(dq) is False
    dq.append(now)  # 第 4 次 -> 超过
    assert r.evaluate(dq) is True


def test_anomaly_detector_bans_ip():
    bl = Blacklist(seed=[], file_path="")
    det = AnomalyDetector([FrequencyRule(3, 60, 86400)], bl)
    ip = "5.6.7.8"
    for _ in range(3):
        hit, _ = det.observe(ip)
        assert hit is False
        assert bl.is_blacklisted(ip) is False
    hit, reasons = det.observe(ip)
    assert hit is True
    assert "frequency" in reasons[0]
    assert bl.is_blacklisted(ip) is True
    # 其他 IP 不受影响
    assert bl.is_blacklisted("9.9.9.9") is False


def test_middleware_audit_records_to_db(tmp_path):
    db = tmp_path / "audit.db"
    settings = Settings(
        enable_exploration=False,
        audit_enabled=True,
        audit_db_url=f"sqlite:///{db}",
        audit_log_file="",
        anomaly_enabled=False,
        auth_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app)
    client.get("/api/meta")

    store = app.state.audit_store
    assert store is not None
    assert asyncio.run(store.count_audit()) >= 1

    async def _last_row():
        async with store._session() as s:
            return await s.scalar(select(AuditLog).order_by(AuditLog.id.desc()).limit(1))

    row = asyncio.run(_last_row())
    assert row.path == "/api/meta"
    assert row.status_code == 200
    assert row.client_ip
    assert row.client_device  # 设备推断非空


def test_middleware_anomaly_bans_after_threshold():
    settings = Settings(
        enable_exploration=False,
        trust_proxy=True,
        audit_enabled=False,
        anomaly_enabled=True,
        anomaly_frequency_max=3,
        anomaly_frequency_window=60,
        anomaly_ban_seconds=86400,
        auth_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app)
    headers = {"X-Forwarded-For": "9.9.9.9"}

    # 前 3 次正常（频率未超）
    for _ in range(3):
        assert client.get("/api/meta", headers=headers).status_code == 200
    # 第 4 次命中频率规则：本次仍 200，但随后被拉黑
    assert client.get("/api/meta", headers=headers).status_code == 200
    # 之后该 IP 被封禁
    assert client.get("/api/meta", headers=headers).status_code == 403
    # 其他 IP 不受影响
    assert client.get("/api/meta", headers={"X-Forwarded-For": "1.1.1.1"}).status_code == 200


def test_middleware_audit_disabled_skips_db(tmp_path):
    db = tmp_path / "audit.db"
    settings = Settings(
        enable_exploration=False,
        audit_enabled=False,
        audit_db_url=f"sqlite:///{db}",
        auth_enabled=False,
    )
    app = create_app(settings)
    client = TestClient(app)
    client.get("/api/meta")
    # 审计关闭时不应初始化/写入审计库
    assert app.state.audit_store is None
