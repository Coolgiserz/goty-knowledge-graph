"""图查询路由：在统一接口之上暴露「更丰富的检索 / 遍历」能力。

这些端点**只读、轻量**，无论探索开关是否开启都可访问；底层由 :mod:`api.graph_store`
的抽象后端支撑 —— 默认走内存 networkx，配置 ``GOTY_GRAPH_BACKEND=neo4j`` 后自动切换
到 Cypher 查询，无需改动路由代码。

- ``GET /api/graph/search``   关键词检索节点（标签/标题/名称/开发商…）
- ``GET /api/graph/node/{id}`` 取单个节点详情
- ``GET /api/graph/traverse``  以某节点为中心做多跳邻居展开（子图，供可视化）
- ``GET /api/graph/path``      两节点间最短路径

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
