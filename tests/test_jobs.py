"""异步任务接口测试：创建 / 轮询 / 列表 / 队列 / 取消，以及排队位次语义（单元级）。

创建/取消等接口层用桩替换重计算以保证快速与确定性；排队位次与全局负荷的语义则
直接在 :class:`api.tasks.TaskManager` 上做确定性断言。
"""

import pytest
from api.registry import run_board as _real_run_board
from api.tasks import Task, TaskManager


@pytest.fixture
def stub_run_board(monkeypatch):
    def fake(name, params, data_matches_baseline):
        return {
            "board": name,
            "params": params,
            "interpretation": "x",
            "validity": {
                "data_matches_baseline": True,
                "interpretation_valid": True,
                "invalid_reasons": [],
            },
            "panels": [],
            "tables": [],
            "metrics": {},
        }

    monkeypatch.setattr("api.registry.run_board", fake)
    yield
    monkeypatch.setattr("api.registry.run_board", _real_run_board)


def test_create_and_get_job(client_enabled, stub_run_board):
    r = client_enabled.post("/api/jobs", json={"board": "community", "params": {}})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"id", "status", "board", "owner"}
    assert body["board"] == "community"

    job_id = body["id"]
    r2 = client_enabled.get(f"/api/jobs/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["id"] == job_id


def test_list_jobs_includes_own(client_enabled, stub_run_board):
    client_enabled.post("/api/jobs", json={"board": "community", "params": {}})
    r = client_enabled.get("/api/jobs")
    assert r.status_code == 200
    assert r.json()["scope"] == "self"
    assert len(r.json()["jobs"]) >= 1


def test_queue_status_shape(client_enabled, stub_run_board):
    r = client_enabled.get("/api/jobs/queue")
    assert r.status_code == 200
    q = r.json()["queue"]
    assert set(q) == {"running", "waiting", "max_workers"}
    assert q["max_workers"] >= 1


def test_cancel_unknown_returns_404(client_enabled, stub_run_board):
    r = client_enabled.delete("/api/jobs/nope")
    assert r.status_code == 404


def test_jobs_blocked_when_disabled(client_disabled):
    r = client_disabled.post("/api/jobs", json={"board": "community", "params": {}})
    assert r.status_code == 403
    r2 = client_disabled.get("/api/jobs")
    assert r2.status_code == 403


def test_queue_position_semantics():
    """确定性断言排队位次与全局负荷语义。"""
    mgr = TaskManager(max_workers=2, max_pending_per_owner=5)
    a = Task("a", "community", {}, "o")
    a.status = "running"
    a.created_at = 0.0
    b = Task("b", "community", {}, "o")
    b.status = "pending"
    b.created_at = 1.0
    c = Task("c", "community", {}, "o")
    c.status = "pending"
    c.created_at = 2.0
    mgr._tasks = {"a": a, "b": b, "c": c}

    # 位次仅对 pending 任务有意义（running 任务不占队列位次，返回 None）
    assert mgr.queue_position("a") is None  # 运行中
    # b 之前有更早创建且处于运行态的 a -> 位次 = 1 + 1 = 2
    assert mgr.queue_position("b") == 2
    # c 之前有 a(running) + b(pending) 两个更早的占用/等待任务 -> 位次 3
    assert mgr.queue_position("c") == 3

    # 已完成任务不在队列中
    c.status = "done"
    assert mgr.queue_position("c") is None

    stats = mgr.queue_stats()
    assert stats == {"running": 1, "waiting": 1, "max_workers": 2}


def test_too_many_pending_rejected():
    """单 owner 待处理超过上限即拒绝。"""
    import threading

    import api.registry as reg

    release = threading.Event()

    def blocked(*args, **kwargs):
        release.wait(10)  # 阻塞直到测试结束释放，确保任务停留在 pending/running
        return {
            "board": "community",
            "params": {},
            "interpretation": "",
            "validity": {},
            "panels": [],
            "tables": [],
            "metrics": {},
        }

    orig = reg.run_board
    reg.run_board = blocked
    mgr = TaskManager(max_workers=2, max_pending_per_owner=2)
    try:
        mgr.create("community", {}, "owner1")
        mgr.create("community", {}, "owner1")
        with pytest.raises(Exception):  # TooManyPending
            mgr.create("community", {}, "owner1")
    finally:
        release.set()
        reg.run_board = orig
