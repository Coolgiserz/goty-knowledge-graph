"""节点类型配置表（api.schema）的单测：验证词汇表外置后派生关系正确。

轻量重构（v0.11）把散落硬编码的「节点类型词汇表」收口到 ``api.schema.GRAPH_SCHEMA``
这一张表。本文件守住「改表即改全行为」的契约：scope 目录、路由校验、Cypher 谓词、
Neo4j 标签反查都从这里派生，不得回潮成字面量。
"""
from api.schema import (
    GRAPH_SCHEMA,
    group_of_node,
    group_predicate,
    list_scopes,
    scope_ids,
)


def test_schema_covers_five_business_types():
    assert set(GRAPH_SCHEMA) == {"game", "goty", "genre", "studio", "award"}


def test_list_scopes_starts_with_all_then_projections():
    scopes = list_scopes()
    assert scopes[0]["id"] == "all"
    ids = [s["id"] for s in scopes]
    assert ids == ["all", "game", "goty", "genre", "studio", "award"]
    for s in scopes[1:]:  # 每个同类投影 scope 都带物理含义说明
        assert s["blurb"]


def test_scope_ids_matches_list_scopes():
    assert scope_ids() == {s["id"] for s in list_scopes()}


def test_group_predicate_game_goty_disambiguated_by_is_goty():
    assert group_predicate("game") == "n:Game AND (n.is_goty IS NULL OR n.is_goty = false)"
    assert group_predicate("goty") == "n:Game AND n.is_goty = true"
    assert group_predicate("genre") == "n:Genre"
    assert group_predicate("studio") == "n:Studio"
    assert group_predicate("award") == "n:Award"
    assert group_predicate("bogus") == ""


def test_group_of_node_resolves_game_goty_shared_label():
    assert group_of_node({"Game"}, {"is_goty": False}) == "game"
    assert group_of_node({"Game"}, {"is_goty": True}) == "goty"
    assert group_of_node({"Studio"}, {}) == "studio"
    assert group_of_node({"Genre"}, {}) == "genre"
    assert group_of_node({"Award"}, {}) == "award"
    assert group_of_node({"Unknown"}, {}) == "node"
