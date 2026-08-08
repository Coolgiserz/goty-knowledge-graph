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
from api.audit.store import AuditStore
from api.config import Settings
from api.ratelimit import Blacklist
from fastapi.testclient import TestClient
from sqlalchemy import select


def test_audit_store_sqlite_records(tmp_path):
    db = tmp_path / "audit.db"
    store = AuditStore(f"sqlite:///{db}")
    store.init()
    assert asyncio.run(store.count_audit()) == 0

    asyncio.run(
        store.record_audit(
            {
                "request_id": uuid.uuid4().hex,
                "client_ip": "1.2.3.4",
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
        )
    )
    assert asyncio.run(store.count_audit()) == 1
    assert asyncio.run(store.count_audit("1.2.3.4")) == 1
    assert asyncio.run(store.count_audit("9.9.9.9")) == 0

    asyncio.run(
        store.record_anomaly(
            {
                "request_id": uuid.uuid4().hex,
                "client_ip": "1.2.3.4",
                "rule": "frequency",
                "detail": "frequency:>3/60s",
                "action": "blacklist_86400s",
            }
        )
    )
    assert asyncio.run(store.count_anomalies("1.2.3.4")) == 1


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
    )
    app = create_app(settings)
    client = TestClient(app)
    client.get("/api/meta")
    # 审计关闭时不应初始化/写入审计库
    assert app.state.audit_store is None
