"""图查询路由：在统一接口之上暴露「更丰富的检索 / 遍历」能力。

这些端点**只读、轻量**，无论探索开关是否开启都可访问；底层由 :mod:`api.graph_store`
的抽象后端支撑 —— 默认走内存 networkx，配置 ``GOTY_GRAPH_BACKEND=neo4j`` 后自动切换
到 Cypher 查询，无需改动路由代码。

- ``GET /api/graph/search``   关键词检索节点（标签/标题/名称/开发商…）
- ``GET /api/graph/node/{id}`` 取单个节点详情
- ``GET /api/graph/traverse``  以某节点为中心做多跳邻居展开（子图，供可视化）
- ``GET /api/graph/path``      两节点间最短路径
- ``GET /api/graph/filter``    按类别 + 标签筛选节点并展开（前端「渲染种子」用）
- ``GET /api/graph/communities`` 社区发现（前端「社区分析」用，支持 resolution 参数）
- ``GET /api/graph/influence`` 节点影响力（中心性）排行榜（前端「网络影响力」用）

Neo4j 后端连不上时，工厂已回退到 networkx；若显式选了 neo4j 但驱动未装 / 仍不可用，
查询方法抛 ``neo4j_unavailable``，这里统一转成 503。
"""

from fastapi import APIRouter, Depends, HTTPException

from ..deps import get_graph_store_dep
from ..graph_store import GraphStore

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/graph/search")
def graph_search(q: str = "", limit: int = 20, store: GraphStore = Depends(get_graph_store_dep)):
    """按关键词检索节点（前端搜索框 / 联想）。"""
    try:
        results = store.search(q, limit=max(1, min(limit, 100)))
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    return {"query": q, "backend": store.backend, "results": results}


@router.get("/graph/node/{node_id}")
def graph_node(node_id: str, store: GraphStore = Depends(get_graph_store_dep)):
    """取单个节点详情；不存在返回 404。"""
    try:
        node = store.get_node(node_id)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    if node is None:
        raise HTTPException(status_code=404, detail="node_not_found")
    return node


@router.get("/graph/traverse")
def graph_traverse(
    start: str,
    hops: int = 1,
    types: str | None = None,
    store: GraphStore = Depends(get_graph_store_dep),
):
    """以 ``start`` 为中心做 ``hops`` 跳邻居展开，返回子图（节点 + 关系）。

    ``types`` 可传逗号分隔的关系类型过滤（如 ``DEVELOPED,WON``）。
    """
    try:
        ets = [t.strip() for t in types.split(",")] if types else None
        sub = store.neighbors(start, hops=hops, edge_types=ets)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    if sub["center"] is None:
        raise HTTPException(status_code=404, detail="node_not_found")
    sub["backend"] = store.backend
    return sub


@router.get("/graph/path")
def graph_path(a: str, b: str, store: GraphStore = Depends(get_graph_store_dep)):
    """两节点间最短路径；不可达返回 404。"""
    try:
        path = store.shortest_path(a, b)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    if path is None:
        raise HTTPException(status_code=404, detail="no_path")
    path["backend"] = store.backend
    return path


@router.get("/graph/list")
def graph_list(
    group: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
    store: GraphStore = Depends(get_graph_store_dep),
):
    """浏览节点：按 group 过滤 + 关键词检索 + 分页。前端表格面板用。"""
    if group in (None, "", "all"):
        group = None
    try:
        res = store.list_nodes(group=group, query=q, limit=limit, offset=offset)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    res["backend"] = store.backend
    return res


@router.get("/graph/seed")
def graph_seed(
    group: str | None = None,
    limit: int = 12,
    hops: int = 1,
    store: GraphStore = Depends(get_graph_store_dep),
):
    """按 group 取一批种子节点并展开 ``hops`` 跳，返回合并子图。前端初始画布 / 种子渲染用。"""
    if group in (None, "", "all"):
        group = None
    try:
        res = store.seed(group=group, limit=limit, hops=hops)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    res["backend"] = store.backend
    return res


@router.get("/graph/filter")
def graph_filter(
    group: str | None = None,
    tags: str | None = None,
    limit: int = 50,
    hops: int = 1,
    store: GraphStore = Depends(get_graph_store_dep),
):
    """按类别(group)+标签(tags, 逗号分隔的类型名)筛选节点并展开 ``hops`` 跳。

    前端「渲染种子」用：用户按类别（游戏/工作室/类型/奖项）与标签（genre 名称，
    如 开放世界、角色扮演）挑一批节点作为探索起点。``tags`` 仅在游戏类目下生效。"""
    if group in (None, "", "all"):
        group = None
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    try:
        res = store.filter(group=group, tags=tag_list, limit=limit, hops=hops)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    res["backend"] = store.backend
    return res


@router.get("/graph/communities")
def graph_communities(
    algorithm: str = "modularity",
    resolution: float | None = None,
    store: GraphStore = Depends(get_graph_store_dep),
):
    """社区发现：返回社团汇总（规模 + 成员）与每个节点的社团归属。前端社区分析模式用。

    - ``algorithm``：``modularity``（默认，模块度最大化）或 ``label_propagation``（标签传播）。
    - ``resolution``：仅对 ``modularity`` 生效，控制社团粒度（越大越细碎，默认 1.0）。
    """
    if algorithm not in ("modularity", "label_propagation"):
        algorithm = "modularity"
    params = {}
    if resolution is not None and algorithm == "modularity":
        params["resolution"] = float(resolution)
    try:
        res = store.communities(algorithm=algorithm, params=params or None)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    res["backend"] = store.backend
    return res


@router.get("/graph/influence")
def graph_influence(
    metric: str = "pagerank",
    top_n: int = 20,
    group: str | None = None,
    store: GraphStore = Depends(get_graph_store_dep),
):
    """节点影响力（中心性）排行榜：揭示「谁在图谱里最重要」。

    - ``metric``：``degree``（度数中心性）/ ``pagerank``（默认）/ ``betweenness``（中介中心性）。
    - ``top_n``：返回前 N 名（1–100）。
    - ``group``：按节点类型过滤（game/goty/studio/genre/award），省略表示全类型。
    """
    if metric not in ("degree", "pagerank", "betweenness"):
        metric = "pagerank"
    top_n = max(1, min(int(top_n), 100))
    try:
        res = store.influence(metric=metric, top_n=top_n, group=group)
    except RuntimeError as exc:
        if "neo4j_unavailable" in str(exc):
            raise HTTPException(status_code=503, detail="graph_backend_unavailable") from None
        raise
    res["backend"] = store.backend
    return res
