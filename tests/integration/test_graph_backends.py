"""双图后端（NetworkX / Neo4j）**一致性**集成测试。

为什么必须真连 Neo4j
--------------------
仅靠源码走查或单后端测试，曾有三个缺陷全部漏网（只有真连才暴露）：

1. ``communities`` 的 Cypher ``RETURN a, b`` 却访问 ``rec["r"]`` → KeyError，
   **Neo4j 后端的社区发现接口 100% 不可用**；
2. ``_node_view_from`` 无差别 fallback 取 id，Award 节点拿到 ``game_id`` 与 GOTY 撞车，
   **20 个节点被静默覆盖丢失**（189 → 169 唯一 id），社区划分严重劣化
   （10 团 / Q=0.657 退化为 136 团 / Q=0.184）；
3. ``stats`` 缺 nodes / edges / goty 三个字段，前端切换后端后变 undefined。

运行方式（需要可用的 Neo4j）：::

    bash scripts/neo4j_dev.sh          # 起开发容器 7475/7688 并导入
    uv sync --extra neo4j              # 装 neo4j driver
    make test-integration              # pytest -m integration

无 Neo4j（或未装 driver）时全部跳过，不影响常规 CI。
"""

import os

import pytest
from api.graph_store import Neo4jStore, NetworkXStore

NEO4J_URI = os.getenv("GOTY_NEO4J_TEST_URI", "bolt://localhost:7688")
NEO4J_USER = os.getenv("GOTY_NEO4J_TEST_USER", "neo4j")
NEO4J_PASS = os.getenv("GOTY_NEO4J_TEST_PASSWORD", "pAsSwOrd123")


def _neo4j_store():
    try:
        import neo4j  # noqa: F401
    except ImportError:
        pytest.skip("未安装 neo4j driver（uv sync --extra neo4j）")
    store = Neo4jStore(NEO4J_URI, NEO4J_USER, NEO4J_PASS)
    if not store.is_connected():
        pytest.skip(f"Neo4j 不可用（{NEO4J_URI}）；先执行 bash scripts/neo4j_dev.sh")
    return store


@pytest.mark.integration
def test_all_node_ids_unique_and_not_none():
    """每个节点都必须有唯一且非空的 id —— 这是「节点被静默覆盖」的守护测试。

    回归背景：Award 节点同时带 award_id / game_id，取错就与 GOTY 撞 id，
    189 个节点只剩 169 个唯一 id。
    """
    store = _neo4j_store()
    seen: dict[str, str] = {}
    with store._require().session() as s:
        for rec in s.run("MATCH (n) RETURN n LIMIT 10000"):
            v = store._node_view_from(rec["n"])
            assert v["id"] is not None, f"节点 id 为 None（group={v['group']}）"
            prev = seen.get(v["id"])
            assert prev is None, f"id 冲突：{v['id']} 同时属于 {prev} 与 {v['group']}"
            seen[v["id"]] = v["group"]


@pytest.mark.integration
def test_communities_contract_identical_across_backends():
    """两后端的社区结果必须**字段相同 + 划分相同**（契约与语义双重一致）。"""
    nx_s, nj_s = NetworkXStore(), _neo4j_store()
    a = nx_s.communities(algorithm="modularity")
    b = nj_s.communities(algorithm="modularity")

    assert set(a) == set(b), f"字段不一致：仅nx={set(a) - set(b)} 仅nj={set(b) - set(a)}"
    assert len(a["assignment"]) == len(b["assignment"]), "覆盖节点数应一致"

    def groups(res):
        g: dict = {}
        for n, c in res["assignment"].items():
            g.setdefault(c, set()).add(n)
        return sorted(map(sorted, g.values()))

    assert groups(a) == groups(b), "同一算法的社区划分应完全一致"
    assert a["modularity"] == pytest.approx(b["modularity"], abs=1e-9)


@pytest.mark.integration
def test_animation_frames_share_contract_fields():
    """动画帧字段（含 metric_label 语义标签）跨后端一致。"""
    nx_s, nj_s = NetworkXStore(), _neo4j_store()
    fa = nx_s.communities(algorithm="girvan_newman", animate=True)["frames"]
    fb = nj_s.communities(algorithm="girvan_newman", animate=True)["frames"]
    assert fa and fb, "两后端都应产出动画帧"
    assert set(fa[0]) == set(fb[0])
    assert fa[0]["metric_label"] == fb[0]["metric_label"]


@pytest.mark.integration
def test_stats_fields_identical_across_backends():
    """stats 字段集合必须一致（前端按固定字段渲染）。"""
    nx_s, nj_s = NetworkXStore(), _neo4j_store()
    a, b = nx_s.stats(), nj_s.stats()
    assert set(a) == set(b), f"仅nx={set(a) - set(b)} 仅nj={set(b) - set(a)}"
    for k in ("nodes", "edges", "games", "goty", "studios", "genres", "awards"):
        assert a[k] == b[k], f"{k}: networkx={a[k]} neo4j={b[k]}"


@pytest.mark.integration
def test_method_return_shapes_match_across_backends():
    """其余方法的返回结构（dict 键 / 类型）跨后端一致。"""
    nx_s, nj_s = NetworkXStore(), _neo4j_store()
    node = nx_s.list_nodes(limit=1)["items"][0]
    nid = node["id"]

    for label, fn in [
        ("get_node", lambda s: s.get_node(nid)),
        ("neighbors", lambda s: s.neighbors(nid, hops=2)),
        ("list_nodes", lambda s: s.list_nodes(limit=3)),
        ("seed", lambda s: s.seed(limit=3)),
        ("influence", lambda s: s.influence(top_n=3)),
    ]:
        ra, rb = fn(nx_s), fn(nj_s)
        assert type(ra) is type(rb), f"{label}: 类型不一致 {type(ra)} vs {type(rb)}"
        if isinstance(ra, dict):
            assert set(ra) == set(rb), f"{label} 字段不一致：{set(ra) ^ set(rb)}"
