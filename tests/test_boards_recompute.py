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


# --------------------------------------------------------------------------- #
# 图浏览器社区发现的退化图边界（api/community.py，供 /api/graph/communities 使用）
# --------------------------------------------------------------------------- #
def _degenerate_graphs():
    import networkx as nx

    g_empty = nx.Graph()
    g_single = nx.Graph()
    g_single.add_node("a")
    g_isolated = nx.Graph()
    g_isolated.add_nodes_from(["a", "b"])  # 两孤立节点、0 边
    g_selfloop = nx.Graph()
    g_selfloop.add_edge("a", "a")
    return {
        "空图": g_empty,
        "单点": g_single,
        "两孤立节点": g_isolated,
        "仅自环": g_selfloop,
    }


@pytest.mark.parametrize("gname", ["空图", "单点", "两孤立节点", "仅自环"])
def test_community_detection_survives_degenerate_graphs(gname):
    """退化图不得让任何已安装的算法崩溃（返回 500）。

    回归背景：girvan_newman 的动画帧描述对 ``modularity=None`` 未做保护，
    两孤立节点（0 边）时 ``f"...Q={q:.3f}"`` 直接抛 TypeError；
    而 detect() 无条件生成动画帧，故即使不请求动画也会崩。
    """
    from api.community import list_detectors, run_detection

    G = _degenerate_graphs()[gname]
    for d in list_detectors():
        name = d["name"]
        try:
            run_detection(name, G, {})
        except RuntimeError:
            continue  # 缺可选依赖（如 louvain/infomap 未装），属预期
        except Exception as e:  # 其余任何异常都是缺陷
            pytest.fail(f"{gname} + {name} 崩溃：{type(e).__name__}: {e}")


def test_girvan_newman_frames_describe_uncomputable_modularity():
    """退化图下动画帧描述不得因模块度为 None 而崩，且要给出可读文案。"""
    from api.community import run_detection

    G = _degenerate_graphs()["两孤立节点"]
    res = run_detection("girvan_newman", G, {}, animate=True)
    assert res.frames, "animate=True 应产出动画帧"
    for f in res.frames:
        assert "Q=" not in f.description or "无法计算" in f.description or "." in f.description
        assert isinstance(f.description, str) and f.description


def test_detect_does_not_recompute_animation_frames():
    """未请求动画时不应生成动画帧——此前 4 个 detector 无条件重跑一遍算法填 frames，
    而 run_detection 在 animate=True 时本就会填，导致每次调用双倍计算。"""
    from api.community import run_detection

    # 用「两孤立节点」：它有可分裂步骤、能产出动画帧，便于对比 animate 开关的效果。
    # （「仅自环」图无可切边、生成器为空，两种情况都没有帧，不适合做对比。）
    G = _degenerate_graphs()["两孤立节点"]
    res = run_detection("girvan_newman", G, {}, animate=False)
    assert res.frames == [], "animate=False 时不应生成动画帧"
    res2 = run_detection("girvan_newman", G, {}, animate=True)
    assert res2.frames, "animate=True 时才生成动画帧"
