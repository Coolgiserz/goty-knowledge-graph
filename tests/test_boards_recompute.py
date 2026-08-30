"""探索板块**真实计算**冒烟测试（不使用 stub）。

背景：``tests/test_boards.py`` 把 ``registry.run_board`` 整体 stub 掉了，只覆盖接口层
（鉴权 / 路由 / response_model），**重计算本身零测试覆盖**——算法改坏、依赖升级导致
数值异常、参数裁剪失效，都不会有任何用例报警。本文件补上这一层。

设计取舍（避免 flaky）：
- 只断言**结构合规与取值范围**，不断言精确数值：KMeans / Louvain / node2vec 的结果
  会随依赖版本小幅波动，锁死具体数字只会制造噪音。
- 已实测 5 个板块在默认参数下**结果确定性**（连跑两次 metrics 完全相同，内部有固定
  随机种子），总耗时约 2.5s，故放在常规测试而非 integration 标记下。
- 不断言 ``validity.data_matches_baseline`` 为 True：数据漂移是该字段的**预期输出**，
  当前基线即不匹配（预写解读失效是设计内的行为）。
"""

import pytest
from api import tools  # noqa: F401  导入即注册板块，必须保留
from api.registry import all_boards, run_board

BOARD_NAMES = [b.name for b in all_boards()]


def test_all_boards_registered():
    assert set(BOARD_NAMES) == {"cluster", "community", "goty", "hotspot", "studio"}


@pytest.mark.parametrize("name", BOARD_NAMES)
def test_board_runs_and_returns_valid_structure(name):
    """每个板块用默认参数都能跑通，且返回结构符合前端契约。"""
    res = run_board(name, {}, data_matches_baseline=True)
    assert res is not None, f"板块 {name} 未注册或返回 None"

    assert res["board"] == name
    assert isinstance(res["params"], dict)

    # 前端依赖这三个字段渲染：缺任一都会白屏
    assert isinstance(res["panels"], list) and res["panels"], "panels 不能为空"
    assert isinstance(res["tables"], list)
    assert isinstance(res["metrics"], dict) and res["metrics"], "metrics 不能为空"
    for p in res["panels"]:
        assert {"type", "title", "data"} <= set(p), f"panel 结构不合规：{sorted(p)}"

    # 有效性判定：结构固定，且理由与结论自洽
    v = res["validity"]
    assert {"data_matches_baseline", "interpretation_valid", "invalid_reasons"} <= set(v)
    assert v["interpretation_valid"] is True, "默认参数下预写解读应当有效"
    assert v["invalid_reasons"] == [], "默认参数不应产生失效理由"

    assert isinstance(res["interpretation"], str) and res["interpretation"], "解读文本不能为空"


@pytest.mark.parametrize("name", BOARD_NAMES)
def test_board_metrics_are_in_sane_range(name):
    """关键指标落在合理范围（不锁死精确值，只防「算崩了还返回 200」）。"""
    res = run_board(name, {}, data_matches_baseline=True)
    m = res["metrics"]

    if name == "community":
        assert m["n_communities"] >= 1, "社区数至少为 1"
        assert -1.0 <= m["quality"]["modularity"] <= 1.0, "模块度超出合法区间"
    elif name == "cluster":
        assert m["best_k"] >= 2, "最佳簇数至少为 2"
    elif name == "studio":
        assert m["n_studios"] >= 1
        assert -1.0 <= m["spearman_rho"] <= 1.0, "相关系数超出 [-1,1]"
    elif name == "goty":
        assert m["n_seeds"] >= 1
        assert 0.0 < m["alpha"] < 1.0, "alpha 应在 (0,1)"
    elif name == "hotspot":
        assert m["n_first_half"] >= 0 and m["n_second_half"] >= 0
        assert 2006 <= m["split_year"] <= 2025, "分割年份超出数据覆盖区间"


def test_unknown_board_returns_none():
    assert run_board("no_such_board", {}, True) is None


def test_out_of_range_params_are_clamped_not_honored():
    """越界参数必须被裁剪后执行，不得原样传给算法（否则即为 DoS 入口）。

    回归背景：``ParamSpec.coerce`` 曾声明 min/max 却从不裁剪，studio.num_walks
    （声明 max=60）传 6000 时耗时从 1s 涨到 62s、60000 直接 OOM。
    """
    from api.registry import get

    tool = get("studio")
    spec = next(p for p in tool.params if p.key == "num_walks")
    oversized = spec.max * 10

    # 直接验证 coerce：越界值被裁到 max
    assert spec.coerce(oversized) == spec.max

    # 端到端：请求级传越界值，run_board 内部走 coerce 后应正常返回且耗时可控
    res = run_board("studio", {"num_walks": oversized}, data_matches_baseline=True)
    assert res is not None
    assert res["params"]["num_walks"] == spec.max, "执行的参数应已被裁剪"
