"""探索板块计算接口测试：/api/board/{name} 的开关守卫、404、与响应结构。

重计算本身（analysis/ml 流水线）由上游测试覆盖；此处用轻量桩替换 ``run_board``，
专注验证接口层（鉴权 / 路由 / response_model 约束）是否一致。
"""

import pytest
from api import registry
from api.registry import run_board as _real_run_board


@pytest.fixture
def stub_run_board(monkeypatch):
    known = {b.name for b in registry.all_boards()}

    def fake(name, params, data_matches_baseline):
        if name not in known:
            return None  # 复刻真实 run_board 对未知板块返回 None -> 404
        return {
            "board": name,
            "params": params,
            "interpretation": "demo interpretation",
            "validity": {
                "data_matches_baseline": data_matches_baseline,
                "interpretation_valid": True,
                "invalid_reasons": [],
            },
            "panels": [{"type": "bar", "title": "x", "data": [1, 2]}],
            "tables": [{"title": "t", "columns": ["a"], "rows": [[1]]}],
            "metrics": {"n": 3},
        }

    monkeypatch.setattr("api.registry.run_board", fake)
    yield
    monkeypatch.setattr("api.registry.run_board", _real_run_board)


def test_board_sync_blocked_when_disabled(client_disabled):
    r = client_disabled.post("/api/board/community", json={"params": {}})
    assert r.status_code == 403
    assert r.json()["detail"] == "exploration_disabled"


def test_board_sync_runs_when_enabled(client_enabled, stub_run_board):
    r = client_enabled.post("/api/board/community", json={"params": {"k": 1}})
    assert r.status_code == 200
    body = r.json()
    assert body["board"] == "community"
    # response_model 已做类型约束
    assert body["validity"]["interpretation_valid"] is True
    assert body["metrics"]["n"] == 3
    assert body["panels"][0]["type"] == "bar"


def test_board_unknown_returns_404(client_enabled, stub_run_board):
    r = client_enabled.post("/api/board/does_not_exist", json={"params": {}})
    assert r.status_code == 404
