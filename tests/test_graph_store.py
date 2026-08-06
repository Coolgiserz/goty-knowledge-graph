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
    return TestClient(create_app(Settings()))


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
