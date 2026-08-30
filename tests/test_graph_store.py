"""图查询后端与端点测试。

默认后端为内存 networkx（离线可用），这里重点覆盖它；Neo4j 后端的「连不上即回退」
行为用确定性单测验证（不依赖真实数据库 / docker）。
"""

import pytest
from api.app import create_app
from api.config import Settings
from api.graph_store import Neo4jStore, NetworkXStore, get_graph_store
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # 默认配置：graph_backend=networkx，探索关闭（图查询端点无需探索开关）。
    return TestClient(create_app(Settings(auth_enabled=False)))


# ---------- 端点：默认 networkx 后端 ----------


def test_meta_reports_networkx_backend(client):
    r = client.get("/api/meta")
    assert r.status_code == 200
    assert r.json()["graph_backend"] == "networkx"


def test_search_finds_node(client):
    r = client.get("/api/graph/search", params={"q": "上古卷轴", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "networkx"
    ids = [n["id"] for n in body["results"]]
    assert "game_001" in ids


def test_search_empty_query(client):
    r = client.get("/api/graph/search", params={"q": ""})
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_get_node(client):
    r = client.get("/api/graph/node/game_001")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "game_001"
    assert body["group"] == "goty"


def test_get_node_404(client):
    r = client.get("/api/graph/node/does_not_exist")
    assert r.status_code == 404


def test_traverse_studio(client):
    r = client.get("/api/graph/traverse", params={"start": "studio_bethesda", "hops": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["center"]["id"] == "studio_bethesda"
    assert body["backend"] == "networkx"
    ids = [n["id"] for n in body["nodes"]]
    assert "game_001" in ids  # Bethesda 开发了上古卷轴IV


def test_traverse_unknown_center_404(client):
    r = client.get("/api/graph/traverse", params={"start": "nope"})
    assert r.status_code == 404


def test_traverse_type_filter(client):
    r = client.get(
        "/api/graph/traverse",
        params={"start": "studio_bethesda", "hops": 1, "types": "WON"},
    )
    assert r.status_code == 200
    # Bethesda 与 game_001 之间是 DEVELOPED，不是 WON；过滤后应无该邻居
    ids = [n["id"] for n in r.json()["nodes"]]
    assert "game_001" not in ids


def test_path_studio_to_game(client):
    r = client.get("/api/graph/path", params={"a": "studio_bethesda", "b": "game_001"})
    assert r.status_code == 200
    body = r.json()
    assert body["length"] == 1
    assert [n["id"] for n in body["nodes"]] == ["studio_bethesda", "game_001"]


def test_path_no_path_or_same(client):
    # 同一节点：直接返回 None -> 404
    r = client.get("/api/graph/path", params={"a": "game_001", "b": "game_001"})
    assert r.status_code == 404


# ---------- 后端单元：NetworkXStore ----------


def test_networkx_store_unit():
    s = NetworkXStore()
    assert s.backend == "networkx"
    assert s.get_node("game_001")["group"] == "goty"
    assert s.get_node("missing") is None
    path = s.shortest_path("studio_bethesda", "game_001")
    assert path is not None and path["length"] == 1
    nb = s.neighbors("studio_bethesda", hops=1)
    assert any(n["id"] == "game_001" for n in nb["nodes"])
    st = s.stats()
    assert st["nodes"] > 0 and st["games"] > 0


# ---------- Neo4j 后端：连不上即回退（确定性，不需真实库）----------


def test_neo4j_unreachable_not_connected():
    # 连一个不存在的端口：构造不应抛异常，仅标记未连接。
    s = Neo4jStore("bolt://127.0.0.1:59999", "neo4j", "x")
    assert s.is_connected() is False


def test_neo4j_fallback_to_networkx():
    # 显式选 neo4j 但不可达 -> 工厂回退 networkx。
    fb = get_graph_store(Settings(graph_backend="neo4j", neo4j_uri="bolt://127.0.0.1:59999"))
    assert fb.backend == "networkx"


# ---------- 端点：list / seed（前端表格 + 种子渲染）----------


def test_list_filter_group(client):
    r = client.get("/api/graph/list", params={"group": "goty", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "networkx"
    assert body["total"] == 20
    assert all(n["group"] == "goty" for n in body["items"])


def test_list_query_and_pagination(client):
    r = client.get("/api/graph/list", params={"q": "上古", "limit": 100})
    assert r.status_code == 200
    assert r.json()["total"] >= 1
    first = client.get("/api/graph/list", params={"limit": 1, "offset": 0}).json()
    second = client.get("/api/graph/list", params={"limit": 1, "offset": 1}).json()
    assert first["items"][0]["id"] != second["items"][0]["id"]


def test_seed_goty_returns_subgraph(client):
    r = client.get("/api/graph/seed", params={"group": "goty", "limit": 10, "hops": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["center"] is None
    assert body["backend"] == "networkx"
    assert len(body["nodes"]) > 10  # 10 个种子 + 它们的邻居
    assert len(body["edges"]) > 0


def test_seed_all_group(client):
    r = client.get("/api/graph/seed", params={"group": "all", "limit": 5, "hops": 1})
    assert r.status_code == 200
    assert len(r.json()["nodes"]) >= 5


# ---------- 端点 + 单元：渲染种子（按类别/标签筛选）----------


def test_filter_by_tag_open_world(client):
    r = client.get("/api/graph/filter", params={"tags": "开放世界", "hops": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "networkx"
    assert body["tags"] == ["开放世界"]
    assert len(body["nodes"]) > 0
    assert any(n["group"] in ("game", "goty") for n in body["nodes"])


def test_filter_goty_with_genre(client):
    r = client.get("/api/graph/filter", params={"group": "goty", "tags": "角色扮演", "hops": 1})
    assert r.status_code == 200
    body = r.json()
    goty_ids = {n["id"] for n in body["nodes"] if n["group"] == "goty"}
    # 上古卷轴IV 是 GOTY 且属角色扮演，应被筛出
    assert "game_001" in goty_ids


def test_filter_multi_tag_union(client):
    r = client.get("/api/graph/filter", params={"tags": "角色扮演,射击", "hops": 0})
    assert r.status_code == 200
    body = r.json()
    single = client.get("/api/graph/filter", params={"tags": "射击", "hops": 0}).json()
    # 多标签取并集，规模应不小于单个标签
    assert len(body["nodes"]) >= len(single["nodes"])


def test_filter_by_group_only(client):
    r = client.get("/api/graph/filter", params={"group": "studio", "hops": 1})
    assert r.status_code == 200
    body = r.json()
    assert len(body["nodes"]) > 0
    assert any(n["group"] == "studio" for n in body["nodes"])


def test_networkx_store_filter_unit():
    s = NetworkXStore()
    res = s.filter(group=None, tags=["开放世界"], limit=200, hops=1)
    assert len(res["nodes"]) > 0
    assert any(n["group"] in ("game", "goty") for n in res["nodes"])


# ---------- 后端单元：list / seed ----------


def test_networkx_store_list_and_seed_unit():
    s = NetworkXStore()
    lst = s.list_nodes(group="studio", limit=3)
    assert lst["total"] == 15
    assert len(lst["items"]) == 3
    sd = s.seed(group="goty", limit=5, hops=1)
    assert sd["center"] is None
    assert len(sd["nodes"]) > 5
    assert len(sd["edges"]) > 0


# ---------- 端点 + 单元：社区分析（前端社区分析模式）----------


def test_communities_modularity(client):
    r = client.get("/api/graph/communities", params={"algorithm": "modularity"})
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] == "networkx"
    assert body["algorithm"] == "modularity"
    assert len(body["communities"]) > 0
    # 每个节点都带 community 归属，且总规模等于节点数
    total_size = sum(c["size"] for c in body["communities"])
    assert total_size == len(body["nodes"])
    assert all(("community" in n) for n in body["nodes"])
    # 社团按规模降序
    sizes = [c["size"] for c in body["communities"]]
    assert sizes == sorted(sizes, reverse=True)


def test_communities_label_propagation(client):
    r = client.get("/api/graph/communities", params={"algorithm": "label_propagation"})
    assert r.status_code == 200
    body = r.json()
    assert body["algorithm"] == "label_propagation"
    assert len(body["communities"]) > 0
    # 社团成员与汇总规模要一一对应
    for c in body["communities"]:
        assert len(c["members"]) == c["size"]


def test_communities_invalid_algo_returns_400(client):
    # 非法算法名应如实 400（不静默兜底到默认算法）
    r = client.get("/api/graph/communities", params={"algorithm": "whatever"})
    assert r.status_code == 400
    assert "whatever" in r.json()["detail"]


def test_communities_resolution_query_param(client):
    """resolution 查询参数应传入后端并在响应 params 中回显。"""
    r = client.get("/api/graph/communities", params={"algorithm": "modularity", "resolution": 2.5})
    assert r.status_code == 200
    body = r.json()
    assert body["params"] == {"resolution": 2.5}
    # label_propagation 忽略 resolution，params 应为空
    r2 = client.get(
        "/api/graph/communities", params={"algorithm": "label_propagation", "resolution": 2.5}
    )
    assert r2.json()["params"] == {}


def test_networkx_store_communities_unit():
    s = NetworkXStore()
    res = s.communities(algorithm="modularity")
    # 节点数 = 全图节点数；社团数要合理（>1 且 < 节点数）。
    from api.graph_loader import NODES

    assert len(res["nodes"]) == len(NODES)
    assert 1 < len(res["communities"]) < len(NODES)
    # _compute_communities 在两种后端一致：跑到同一张无向图，结果应稳定可复现
    res2 = s.communities(algorithm="modularity")
    assert [c["size"] for c in res["communities"]] == [c["size"] for c in res2["communities"]]
    # 每个社团含 members 列表（用于前端成员表），且成员总数 = 全图节点数
    total_members = sum(len(c["members"]) for c in res["communities"])
    assert total_members == len(NODES)
    # 返回实际生效参数（modularity 默认 resolution=1.0）
    assert res["params"] == {"resolution": 1.0}


def test_communities_resolution_changes_granularity():
    """resolution 拉开差距时应得到不同的社团粒度（社团数量不同）。"""
    s = NetworkXStore()
    low = s.communities(algorithm="modularity", params={"resolution": 0.6})
    high = s.communities(algorithm="modularity", params={"resolution": 3.0})
    assert low["params"] == {"resolution": 0.6}
    assert high["params"] == {"resolution": 3.0}
    # 高 resolution → 社团更细碎（数量不少于低 resolution）
    assert len(high["communities"]) >= len(low["communities"])


def test_communities_label_propagation_has_no_params():
    s = NetworkXStore()
    res = s.communities(algorithm="label_propagation", params={"resolution": 2.0})
    # label_propagation 忽略 resolution，params 应为空
    assert res["params"] == {}


def test_get_graph_store_neo4j_off_returns_networkx():
    """默认（不开启 neo4j）时，工厂返回内存 networkx 后端且可正常服务。"""
    s = Settings()  # graph_backend 默认 networkx
    store = get_graph_store(s)
    assert store.backend == "networkx"
    res = store.seed(group="goty", limit=12, hops=1)
    assert len(res["nodes"]) > 0


def test_get_graph_store_neo4j_on_but_unreachable_falls_back_at_init():
    """配置了 neo4j 但连不上时，工厂在初始化阶段回退 networkx（而非查询中途静默切换）。"""
    # 指向一个不存在的端口，确保初始化阶段 _try_connect 失败。
    s = Settings(graph_backend="neo4j", neo4j_uri="bolt://localhost:1")
    store = get_graph_store(s)
    assert store.backend == "networkx"
    res = store.seed(group="goty", limit=12, hops=1)
    assert len(res["nodes"]) > 0  # 回退后内存数据可用，但标识如实为 networkx


def test_neo4j_seed_filter_cypher_no_param_in_relationship_range():
    """回归：seed/filter 的可变长关系模式不得用 $hops 参数（Neo4j 报错
    Parameter maps cannot be used in MATCH patterns），必须插值成字面量。

    这是 500 报 `CypherSyntaxError ... MATCH p = (s)-[*1..$hops]-(m)` 的根因。"""
    from api.graph_store import Neo4jStore

    captured: list[tuple[str, dict]] = []

    class FakeNode:
        def __init__(self, gid):
            self._gid = gid

        def get(self, k, default=None):
            if k in ("game_id", "studio_id", "genre_id", "award_id"):
                return self._gid
            return default

    class FakeRecord:
        def __init__(self, **kw):
            self._d = kw

        def __getitem__(self, key):
            return self._d[key]

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **params):
            captured.append((cypher, params))
            if "RETURN n LIMIT" in cypher:
                return [FakeRecord(n=FakeNode("game_001"))]
            if "BELONGS_TO_GENRE" in cypher:
                return [FakeRecord(gm=FakeNode("game_001"))]
            return []

    class FakeDriver:
        def session(self):
            return FakeSession()

    neo = Neo4jStore.__new__(Neo4jStore)
    neo._driver = FakeDriver()
    neo._connected = True
    neo._fallback = None
    neo._using_fallback = False
    neo._node_view_from = lambda node: {"id": node.get("game_id"), "group": "goty"}

    # seed：hops=2 → 关系模式须为 [*1..2] 且不得含 $hops
    neo.seed(group="goty", limit=5, hops=2)
    seed_expand = [c for c, _ in captured if "MATCH p = (s)-[*1.." in c]
    assert seed_expand, "seed 应执行展开查询"
    assert "$hops" not in seed_expand[0], "关系模式不得用 $hops 参数"
    assert "[*1..2]" in seed_expand[0], "hops 应插值成字面量"

    # filter：hops=1（带标签命中）→ 同样插值字面量
    captured.clear()
    neo.filter(group="goty", tags=["角色扮演"], hops=1)
    filt_expand = [c for c, _ in captured if "MATCH p = (s)-[*1.." in c]
    assert filt_expand, "filter 应执行展开查询"
    assert "$hops" not in filt_expand[0]
    assert "[*1..1]" in filt_expand[0]

    # filter：hops=0 → 不展开，只返回命中节点（UNWIND ... RETURN DISTINCT n）
    captured.clear()
    neo.filter(group="goty", tags=[], hops=0)
    assert not any("MATCH p = (s)-[*1.." in c for c, _ in captured), "hops=0 不应执行展开查询"
    assert any("RETURN DISTINCT n" in c for c, _ in captured), "hops=0 应只返回命中节点"


# ---------- 节点影响力（中心性） ----------


def test_networkx_store_influence_pagerank_sorted_and_positive():
    """influence 默认 pagerank：返回降序、分数>0、字段完整。"""
    s = NetworkXStore()
    res = s.influence(metric="pagerank", top_n=10)
    assert res["metric"] == "pagerank"
    assert len(res["results"]) == 10
    scores = [r["score"] for r in res["results"]]
    assert all(sc > 0 for sc in scores)
    assert scores == sorted(scores, reverse=True)  # 降序
    for r in res["results"]:
        assert set(r.keys()) >= {"id", "label", "group", "score"}


def test_networkx_store_influence_group_filter_and_topn():
    """group 过滤只返回该类型；top_n 截断生效。"""
    s = NetworkXStore()
    res = s.influence(metric="degree", top_n=5, group="studio")
    assert all(r["group"] == "studio" for r in res["results"])
    assert len(res["results"]) <= 5


def test_influence_endpoint(client):
    """路由端到端：默认 pagerank 返回前 N（按分数降序）。"""
    r = client.get("/api/graph/influence", params={"metric": "degree", "top_n": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["metric"] == "degree"
    assert len(body["results"]) <= 3
    sc = [x["score"] for x in body["results"]]
    assert sc == sorted(sc, reverse=True)


def test_influence_invalid_metric_falls_back(client):
    r = client.get("/api/graph/influence", params={"metric": "bogus"})
    assert r.status_code == 200
    assert r.json()["metric"] == "pagerank"


# ---------- 社区分析：策略模式 / 多算法 / 动画帧 ----------


def test_community_detector_registry_has_core_algorithms():
    """策略注册表应包含核心算法（含用户点名的 infomap 与新增的 louvain/girvan_newman）。"""
    from api.community import DETECTORS, list_detectors

    for name in ("modularity", "label_propagation", "louvain", "infomap", "girvan_newman"):
        assert name in DETECTORS
    catalog = {d["name"]: d for d in list_detectors()}
    # 零依赖算法必须可用；可选依赖算法标记 available 由环境决定
    assert catalog["modularity"]["available"] is True
    assert catalog["label_propagation"]["available"] is True
    assert catalog["girvan_newman"]["available"] is True
    # 每个算法都暴露参数表单与动画支持标记
    for d in catalog.values():
        assert isinstance(d["params_schema"], list)
        assert isinstance(d["supports_animation"], bool)


def test_greedy_modularity_matches_networkx():
    """自实现贪心合并序列的最终划分应与 networkx greedy_modularity_communities 一致。"""
    from collections import defaultdict

    import networkx as nx
    from api.graph_store import NetworkXStore
    from networkx.algorithms.community import greedy_modularity_communities

    s = NetworkXStore()
    G = nx.Graph()
    G.add_nodes_from(s._nodes.keys())
    seen = set()
    for u in s._adj:
        for v, _ in s._adj[u]:
            k = frozenset((u, v))
            if k in seen:
                continue
            seen.add(k)
            G.add_edge(u, v)
    nx_assign = {}
    for i, c in enumerate(greedy_modularity_communities(G, resolution=1.0)):
        for n in c:
            nx_assign[n] = i
    ours = s.communities(algorithm="modularity")
    our_assign = {n["id"]: n["community"] for n in ours["nodes"]}

    def groups(a):
        g = defaultdict(set)
        for n, c in a.items():
            g[c].add(n)
        return {frozenset(v) for v in g.values()}

    assert groups(nx_assign) == groups(our_assign)


def test_community_animation_frames_returned_when_animate():
    """animate=true 时，支持动画的算法应返回过程帧；帧含 step/description/assignment。

    帧经 contracts 层下发为**稳定契约**（dict），不再直接暴露算法库的 CommFrame 对象：
    换算法库时帧的结构保持不变，前端无需改动。
    """
    s = NetworkXStore()
    for algo in ("modularity", "label_propagation", "girvan_newman"):
        res = s.communities(algorithm=algo, animate=True)
        assert res["supports_animation"] is True
        assert len(res["frames"]) >= 2
        f0 = res["frames"][0]
        assert f0["step"] == 0
        assert isinstance(f0["description"], str) and f0["description"]
        # 每帧 assignment 覆盖全图节点
        assert set(f0["assignment"].keys()) == {n["id"] for n in res["nodes"]}
        # 契约新增：指标自带语义标签（旧前端仍可读数值 metric）
        assert f0["metric_label"] is None or isinstance(f0["metric_label"], str)


def test_communities_result_conforms_to_stable_contract():
    """社区结果必须符合稳定契约：指标列表 + 顶层归属 + 向后兼容字段。

    这是「换算法库不影响前端」的守护测试：只要契约字段在，前端就无需改动。
    """
    s = NetworkXStore()
    res = s.communities(algorithm="modularity")
    # 契约核心字段
    for key in ("algorithm", "display_name", "communities", "assignment", "metrics"):
        assert key in res, f"缺契约字段 {key}"
    # 指标是「列表 + 语义标签」，而非固定 modularity 一处
    assert isinstance(res["metrics"], list) and res["metrics"]
    m0 = res["metrics"][0]
    assert {"key", "label", "value", "higher_is_better"} <= set(m0)
    assert m0["key"] == "modularity" and m0["higher_is_better"] is True
    # 向后兼容：旧的顶层 modularity 仍在（从 metrics 派生）
    assert res["modularity"] == m0["value"]
    # 顶层下发归属，前端不必再从 communities[].members 派生
    assert set(res["assignment"]) == {n["id"] for n in res["nodes"]}


def test_contract_supports_algorithms_with_different_metrics():
    """契约必须能表达「指标不是模块度」的算法（如 Infomap 的 codelength 越小越好）。

    旧结构只有固定的 modularity 字段，Infomap 的 codelength 无处安放、被丢弃；
    前端还把裸 metric 硬编码标成「模块度」，会张冠李戴。
    """
    from dataclasses import replace

    from api.contracts import CODELENGTH, MODULARITY, CommunityDetectionResult

    result = CommunityDetectionResult(
        algorithm="infomap",
        display_name="Infomap（信息流）",
        params={},
        communities=[{"id": 0, "size": 2, "members": ["a", "b"]}],
        assignment={"a": 0, "b": 0},
        # 同一结果可同时携带多项指标（本例：codelength 为主，modularity 作同口径对照）
        metrics=[replace(CODELENGTH, value=4.21), replace(MODULARITY, value=0.55)],
    )
    d = result.to_dict()
    keys = [m["key"] for m in d["metrics"]]
    assert keys == ["codelength", "modularity"]
    # 方向不同：codelength 越小越好
    assert d["metrics"][0]["higher_is_better"] is False
    assert d["metrics"][1]["higher_is_better"] is True
    # 向后兼容：modularity 从 metrics 派生，而非硬编码字段
    assert d["modularity"] == 0.55
    # 主指标是 codelength（列表首项），语义标签可供前端正确渲染
    assert result.primary_metric is not None
    assert result.primary_metric.key == "codelength"
    assert "编码长度" in result.primary_metric.format()


def test_infomap_runs_when_installed(client):
    """infomap 已安装：应返回 200 + 合法划分（覆盖全图节点）。"""
    import importlib.util

    if importlib.util.find_spec("infomap") is None:
        pytest.skip("infomap 未安装，跳过运行验证")
    r = client.get("/api/graph/communities", params={"algorithm": "infomap"})
    assert r.status_code == 200
    body = r.json()
    assert body["algorithm"] == "infomap"
    total = sum(len(c["members"]) for c in body["communities"])
    assert total == len(body["nodes"])


def test_louvain_always_runs_on_networkx_builtin(client):
    """louvain 走 networkx 内置实现，不再依赖 python-louvain，故**恒可用**。

    此前该用例依赖第三方 python-louvain，环境未装时直接 skip —— 等于从未真正跑过。
    改用 networkx 内置后无需跳过，可常态化守住划分正确性。
    """
    r = client.get("/api/graph/communities", params={"algorithm": "louvain"})
    assert r.status_code == 200
    body = r.json()
    assert body["algorithm"] == "louvain"
    total = sum(len(c["members"]) for c in body["communities"])
    assert total == len(body["nodes"])
    # 契约：指标自带语义标签
    assert body["metrics"] and body["metrics"][0]["key"] == "modularity"
    assert body["modularity"] == body["metrics"][0]["value"]


def test_louvain_same_result_in_both_entrypoints():
    """同一算法在「探索板块」与「图浏览器」两个入口必须给出一致的划分。

    回归背景：两入口曾用不同底层库（analysis 侧 networkx 内置 vs api 侧 python-louvain
    的 best_partition），同一算法名可能给出不同结果。统一到 networkx 内置并对齐默认
    随机种子（randomize=False -> seed=42）后，两边应完全一致。
    """
    from api import tools  # noqa: F401  触发板块注册
    from api.community import get_detector
    from api.graph_loader import GG
    from api.registry import run_board

    # 入口 A：探索板块（analysis/ml）
    board = run_board("community", {}, True)["metrics"]
    # 入口 B：图浏览器（api/community.py）
    res = get_detector("louvain").detect(GG, {})

    assert board["method"] == "louvain"
    assert len(set(res.assignment.values())) == board["n_communities"]
    assert round(res.modularity, 4) == board["quality"]["modularity"]


def test_louvain_seed_fixed_unless_randomized():
    """``randomize=False`` 必须固定种子，与 analysis 侧默认 seed=42 对齐。

    为什么单写这条白盒断言：只靠「两入口结果一致」守不住种子对齐——本图社团结构明显，
    即便 seed 退化为随机，两边仍可能偶然得到相同划分（实测变异存活）。故直接断言
    种子取值本身，把「对齐意图」变成确定性契约。
    """
    from api.community import get_detector

    det = get_detector("louvain")
    assert det._seed(False) == 42, "randomize=False 应固定种子以保证可复现与两侧一致"
    assert det._seed(True) is None, "randomize=True 才交给算法自身随机"


def test_louvain_animation_frames_preserved():
    """改用 networkx 内置后，多级动画能力必须保留（一级一帧）。"""
    from api.community import get_detector
    from api.graph_loader import GG

    det = get_detector("louvain")
    assert det.supports_animation is True
    frames = list(det.detect_stepwise(GG, {}))
    assert len(frames) >= 1, "至少应有一级划分帧"
    # 帧序号连续、描述可读、每帧覆盖全图节点
    for i, f in enumerate(frames):
        assert f.step == i
        assert "级" in f.description
        assert set(f.assignment) == set(GG.nodes())


def test_unavailable_optional_dependency_returns_400(client, monkeypatch):
    """可选依赖缺失时，路由应返回清晰 400（不静默兜底）。"""
    from api.community import InfomapDetector

    monkeypatch.setattr(InfomapDetector, "available", classmethod(lambda cls: False))
    r = client.get("/api/graph/communities", params={"algorithm": "infomap"})
    assert r.status_code == 400
    assert "infomap" in r.json()["detail"]


def test_community_scope_projection_game():
    """scope=game 做单向投影：只返回游戏节点 + 带权边；覆盖全部游戏。"""
    from api.graph_loader import NODES

    game_ids = {n["id"] for n in NODES if n.get("group") == "game"}
    s = NetworkXStore()
    res = s.communities(algorithm="modularity", scope="game")
    assert res["scope"] == "game"
    assert {n["id"] for n in res["nodes"]} == game_ids
    # 投影边带 weight（共享异类邻居数）
    assert res["edges"] and all("weight" in e for e in res["edges"])
    assert 1 < len(res["communities"]) <= len(game_ids)


def test_community_scope_projection_studio_sparse():
    """studio 投影：本数据里工作室各自出品、少有重叠，应多数为独立簇。"""
    from api.graph_loader import NODES

    studio_n = sum(1 for n in NODES if n.get("group") == "studio")
    s = NetworkXStore()
    res = s.communities(algorithm="modularity", scope="studio")
    assert res["scope"] == "studio"
    assert len(res["nodes"]) == studio_n
    # 绝大多数工作室独立成团（与数据一致：少有共同开发的游戏），允许少量合并
    assert sum(1 for c in res["communities"] if c["size"] == 1) >= studio_n - 4


def test_community_scope_all_returns_full_graph():
    """scope=all（默认）返回完整异质图的全部节点。"""
    from api.graph_loader import NODES

    s = NetworkXStore()
    res = s.communities(algorithm="modularity", scope="all")
    assert {n["id"] for n in res["nodes"]} == {n["id"] for n in NODES}


def test_community_scope_invalid_returns_400(client):
    """非法分析范围应返回 400。"""
    r = client.get("/api/graph/communities", params={"algorithm": "modularity", "scope": "planet"})
    assert r.status_code == 400
    assert "planet" in r.json()["detail"]


def test_girvan_newman_returns_valid_partition():
    """girvan_newman 应返回合法划分（社团数随 target_communities 变化）。"""
    s = NetworkXStore()
    res = s.communities(algorithm="girvan_newman", params={"target_communities": 4})
    # 社团数应 >= 目标（达到目标即停），且覆盖全图节点
    assert len(res["communities"]) >= 4
    total = sum(len(c["members"]) for c in res["communities"])
    assert total == len(res["nodes"])


# --------------------------------------------------------------------------- #
# 双后端语义一致性（同一请求不能因图后端不同而表现不同）
# --------------------------------------------------------------------------- #
def test_neighbors_hops_capped_consistently_across_backends():
    """hops 上限必须与 Neo4j 后端一致（同为 4），否则返回 hops 与实际展开不符。

    回归背景：NetworkX 后端原样执行 hops（无上限），Neo4j 后端内部 clamp 到 4；
    路由层也未校验即透传 —— 同一请求两后端行为不同，前端看到的 hops 是「撒谎」的。
    """
    import inspect

    from api.graph_store import Neo4jStore

    # 两个后端声明的上限必须一致（Neo4j 内部 clamp 表达式与 NetworkX 对齐）
    src = inspect.getsource(Neo4jStore.neighbors)
    assert "min(hops, 4)" in src, "Neo4j 后端应 clamp hops 到 4"

    s = NetworkXStore()
    node = s.list_nodes(limit=1)["items"][0]
    r = s.neighbors(node["id"], hops=100)
    assert r["hops"] == 4, f"超上限的 hops 应被裁剪为 4，实际 {r['hops']}"
    # 下限同样生效
    assert s.neighbors(node["id"], hops=-5)["hops"] == 1
    assert s.neighbors(node["id"], hops=0)["hops"] == 1


def test_communities_payload_shape_is_backend_independent():
    """社区结果的字段集合必须与图后端无关（networkx / neo4j 共用同一出口）。

    回归背景：Neo4j 后端曾直接拼旧结构（缺 assignment / metrics、帧还是裸对象），
    与走契约的 NetworkX 后端字段不一致。现两者共用 _communities_payload。
    """
    import inspect

    from api.graph_store import Neo4jStore

    # 两个后端的 communities 都必须调用共享契约出口（源码级一致性检查，
    # 因为 Neo4j 无可连实例，无法做运行时对比）
    for cls in (NetworkXStore, Neo4jStore):
        body = inspect.getsource(cls.communities)
        assert "_communities_payload" in body, f"{cls.__name__}.communities 应走共享契约出口"

    # 实测 NetworkX 后端字段完整（Neo4j 无可连实例，用共享出口保证其形状一致）
    res = NetworkXStore().communities(algorithm="modularity")
    expected = {
        "algorithm",
        "display_name",
        "params",
        "communities",
        "assignment",
        "metrics",
        "modularity",
        "supports_animation",
        "frames",
        "scope",
        "nodes",
        "edges",
    }
    assert expected <= set(res), f"缺字段：{expected - set(res)}"
