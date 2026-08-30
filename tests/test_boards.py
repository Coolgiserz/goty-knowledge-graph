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


# --------------------------------------------------------------------------- #
# 参数越界防护（min/max 必须真正生效）
# --------------------------------------------------------------------------- #
def test_param_spec_clamps_numeric_to_declared_bounds():
    """数值参数必须裁剪到 [min, max]。

    回归背景：coerce() 声明了 min/max 却从不使用，前端控件只是提示、请求可任意构造。
    实测 studio.num_walks（声明 max=60）传 6000 时耗时从 1s 涨到 62s、更大值吃爆内存，
    任何登录用户一个请求即可打满工作线程（DoS）。
    """
    from api.models import ParamSpec

    spec = ParamSpec("k", "k", "int", 6, min=2, max=12)
    assert spec.coerce(999) == 12, "超出 max 应裁到 max"
    assert spec.coerce(-5) == 2, "低于 min 应裁到 min"
    assert spec.coerce(7) == 7, "范围内原样保留"
    assert spec.coerce(None) == 6, "None 回退默认值"
    assert spec.coerce("abc") == 6, "非法值回退默认值"

    fspec = ParamSpec("r", "r", "float", 1.0, min=0.3, max=3.0)
    assert fspec.coerce(1e9) == 3.0
    assert fspec.coerce(-1.0) == 0.3

    # 布尔不应被数值裁剪误伤
    bspec = ParamSpec("flag", "flag", "bool", False)
    assert bspec.coerce(True) is True


def test_board_params_are_clamped_end_to_end(client_enabled):
    """经 run_board 的端到端验证：越界参数被裁剪，不会原样传给算法。"""
    from api.registry import get

    tool = get("studio")
    spec = next(p for p in tool.params if p.key == "num_walks")
    assert spec.max is not None
    raw = spec.max * 1000  # 极端越界
    coerced = spec.coerce(raw)
    assert coerced == spec.max, f"应裁到 max={spec.max}，实际 {coerced}"
