"""图存储抽象：把「图查询/检索」从具体引擎解耦，便于后端在两种实现间切换。

背景：本项目的数据管线早已存在 —— ``src/build.py`` 以 ``data/graph.json`` 为准导出
CSV，并可由 ``init.cypher`` 导入 Neo4j。但后端 API 一直只用内存 networkx
（见 :mod:`api.graph_loader`）。本模块补上「可选图后端」：

- :class:`NetworkXStore`：默认实现，直接复用 ``graph_loader`` 加载进内存的
  ``NODES`` / ``EDGES``，零额外依赖、离线可用。本就是当前 API 的数据底座。
- :class:`Neo4jStore`：可选实现，经官方 ``neo4j`` driver 用 Cypher 查询。开启方式
  是配置 ``GOTY_GRAPH_BACKEND=neo4j`` 并提供连接信息。driver 为**惰性导入**，
  因此默认安装不需要 ``neo4j`` 包；只有真正切到该后端时才需要
  ``uv pip install ".[neo4j]"``。

工厂 :func:`get_graph_store` 按配置选择后端；若显式选了 neo4j 但连不上，会**自动
回退**到 networkx 并打告警，保证 API 永不因图库不可用而整体崩溃（契合「可选层、
零回退」的落地策略）。

数据模型对齐（graph.json ↔ Neo4j）：
  game / goty  → :Game（``is_goty`` 区分；graph.json 把 GOTY 拆成独立 goty 组，
                  此处按 is_goty 还原成 goty 组，保持两侧检索结果语义一致）
  studio       → :Studio
  genre        → :Genre
  award        → :Award
  关系类型     → DEVELOPED / WON / BELONGS_TO_GENRE / SUBCLASS_OF（两侧一致）
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from .graph_loader import EDGES, NODES

log = logging.getLogger("goty.graph_store")

# 每个 group 取最有代表性的若干 raw 字段，作为前端卡片摘要（避免把整段文案都塞进响应）。
_SUMMARY_FIELDS: dict[str, list[str]] = {
    "game": ["title_zh", "year", "genre", "developer", "player_rating"],
    "goty": ["title_zh", "year", "genre", "developer", "player_rating"],
    "studio": ["name_zh", "country", "founded"],
    "genre": ["name", "parent", "tier"],
    "award": ["name", "year", "body"],
}


def _node_view(n: dict[str, Any]) -> dict[str, Any]:
    """把一个 graph.json 节点渲染成面向前端的精简视图。"""
    group = n.get("group", "")
    raw = n.get("raw", {}) or {}
    fields = _SUMMARY_FIELDS.get(group, [])
    summary = {k: raw.get(k) for k in fields if k in raw}
    return {
        "id": n["id"],
        "group": group,
        "label": n.get("label", ""),
        "summary": summary,
    }


class GraphStore(ABC):
    """图查询接口。两种后端都实现这一组方法，路由层只依赖抽象。"""

    @property
    @abstractmethod
    def backend(self) -> str:
        """后端标识：``"networkx"`` 或 ``"neo4j"``。"""

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """按关键词（节点标签 / 标题 / 名称 / 开发商等）检索节点。"""

    @abstractmethod
    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """按 id 取单个节点视图；不存在返回 ``None``。"""

    @abstractmethod
    def neighbors(
        self, node_id: str, hops: int = 1, edge_types: list[str] | None = None
    ) -> dict[str, Any]:
        """以 ``node_id`` 为中心做多跳邻居展开（无向），返回子图用于可视化。"""

    @abstractmethod
    def shortest_path(self, a: str, b: str) -> dict[str, Any] | None:
        """两节点间最短路径（无向 BFS）；不可达返回 ``None``。"""

    @abstractmethod
    def stats(self) -> dict[str, int]:
        """图规模统计。"""


class NetworkXStore(GraphStore):
    """默认后端：直接复用 ``graph_loader`` 已加载的内存图，构建无向邻接表。"""

    def __init__(self) -> None:
        self._nodes: dict[str, dict[str, Any]] = {n["id"]: n for n in NODES}
        # 无向邻接表：[(邻居 id, 关系类型), ...]，便于多跳与最短路径。
        self._adj: dict[str, list[tuple[str, str]]] = {}
        for e in EDGES:
            f, t, ty = e["from"], e["to"], e.get("type", "")
            self._adj.setdefault(f, []).append((t, ty))
            self._adj.setdefault(t, []).append((f, ty))

    @property
    def backend(self) -> str:
        return "networkx"

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = (query or "").strip().lower()
        if not q:
            return []
        hits: list[dict[str, Any]] = []
        for n in NODES:
            haystack = " ".join(str(v) for v in (n.get("label", ""), n.get("raw") or {})).lower()
            if q in haystack:
                hits.append(_node_view(n))
                if len(hits) >= limit:
                    break
        return hits

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        n = self._nodes.get(node_id)
        return _node_view(n) if n else None

    def neighbors(
        self, node_id: str, hops: int = 1, edge_types: list[str] | None = None
    ) -> dict[str, Any]:
        ets = {t.upper() for t in (edge_types or [])}
        if node_id not in self._nodes:
            return {"center": None, "nodes": [], "edges": [], "hops": hops}
        visited = {node_id}
        frontier = [node_id]
        nodes_out: list[dict[str, Any]] = []
        edges_out: list[dict[str, str]] = []
        for _ in range(max(1, hops)):
            next_frontier: list[str] = []
            for u in frontier:
                for v, ty in self._adj.get(u, []):
                    if ets and ty not in ets:
                        continue
                    edges_out.append({"source": u, "target": v, "type": ty})
                    if v not in visited:
                        visited.add(v)
                        nodes_out.append(self._nodes[v])
                        next_frontier.append(v)
            frontier = next_frontier
            if not frontier:
                break
        return {
            "center": _node_view(self._nodes[node_id]),
            "nodes": [_node_view(n) for n in nodes_out],
            "edges": edges_out,
            "hops": hops,
        }

    def shortest_path(self, a: str, b: str) -> dict[str, Any] | None:
        if a not in self._nodes or b not in self._nodes or a == b:
            return None
        # BFS 记录前驱（含关系类型）找最短路径。
        prev: dict[str, tuple[str | None, str]] = {a: (None, "")}
        frontier = [a]
        found = False
        while frontier and not found:
            nf: list[str] = []
            for u in frontier:
                for v, ty in self._adj.get(u, []):
                    if v in prev:
                        continue
                    prev[v] = (u, ty)
                    if v == b:
                        found = True
                        break
                    nf.append(v)
                if found:
                    break
            frontier = nf
        if b not in prev:
            return None
        seq: list[str] = []
        cur = b
        while cur is not None:
            seq.append(cur)
            p = prev[cur]
            cur = p[0]
        seq.reverse()
        path_nodes = [self._nodes[i] for i in seq]
        path_edges = [
            {"source": seq[i], "target": seq[i + 1], "type": prev[seq[i + 1]][1]}
            for i in range(len(seq) - 1)
        ]
        return {
            "nodes": [_node_view(n) for n in path_nodes],
            "edges": path_edges,
            "length": len(path_edges),
        }

    def stats(self) -> dict[str, int]:
        from .graph_loader import node_counts

        return node_counts()


class Neo4jStore(GraphStore):
    """可选后端：经官方 driver 用 Cypher 查询 Neo4j。

    连接为**惰性**：构造时尝试连接，失败仅打告警并把 ``connected`` 置 False，
    不抛异常（配合 :func:`get_graph_store` 的回退策略）。所有查询方法在
    未连接时抛 ``RuntimeError("neo4j_unavailable")``，由路由层转成 503。
    """

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._uri = uri
        self._user = user
        self._password = password
        self._driver = None
        self._connected = False
        self._try_connect()

    def _try_connect(self) -> None:
        try:
            from neo4j import GraphDatabase
        except ImportError:
            log.warning(
                "neo4j driver 未安装；已选择 neo4j 后端但无法连接。"
                "请执行：uv pip install '.[neo4j]'"
            )
            return
        try:
            self._driver = GraphDatabase.driver(
                self._uri,
                auth=(self._user, self._password),
                connection_timeout=5,
            )
            self._driver.verify_connectivity()
            self._connected = True
            log.info("Neo4j 连接成功：%s", self._uri)
        except Exception as exc:  # 网络/鉴权/版本等任何问题都降级
            log.warning("Neo4j 连接失败，图查询暂不可用：%s", exc)
            self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    @property
    def backend(self) -> str:
        return "neo4j"

    def _require(self) -> Any:
        if not self._connected or self._driver is None:
            raise RuntimeError("neo4j_unavailable")
        return self._driver

    @staticmethod
    def _group_of(node: Any) -> str:
        labels = set(node.labels)
        if "Game" in labels:
            return "goty" if node.get("is_goty") else "game"
        if "Studio" in labels:
            return "studio"
        if "Genre" in labels:
            return "genre"
        if "Award" in labels:
            return "award"
        return "node"

    @staticmethod
    def _label_of(node: Any) -> str:
        g = Neo4jStore._group_of(node)
        if g in ("game", "goty"):
            return f"{node.get('title_zh') or node.get('title')} ({node.get('year')})"
        if g == "studio":
            return node.get("name_zh") or node.get("name") or ""
        if g == "genre":
            return node.get("name") or ""
        if g == "award":
            return f"{node.get('name')} ({node.get('year')})"
        return str(node.id)

    def _node_view_from(self, node: Any) -> dict[str, Any]:
        g = self._group_of(node)
        summary: dict[str, Any] = {}
        for k in _SUMMARY_FIELDS.get(g, []):
            if node.get(k) is not None:
                summary[k] = node.get(k)
        return {
            "id": node.get("game_id")
            or node.get("studio_id")
            or node.get("genre_id")
            or node.get("award_id"),
            "group": g,
            "label": self._label_of(node),
            "summary": summary,
        }

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        driver = self._require()
        q = (query or "").strip()
        if not q:
            return []
        cypher = """
        MATCH (n)
        WHERE n.title_zh CONTAINS $q OR n.title CONTAINS $q
              OR n.name CONTAINS $q OR n.name_zh CONTAINS $q
              OR n.developer CONTAINS $q OR n.body CONTAINS $q
              OR n.label CONTAINS $q
        RETURN n LIMIT $limit
        """
        with driver.session() as s:
            recs = s.run(cypher, q=q, limit=limit)
            return [self._node_view_from(r["n"]) for r in recs]

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        driver = self._require()
        cypher = """
        MATCH (n)
        WHERE n.game_id = $id OR n.studio_id = $id
              OR n.genre_id = $id OR n.award_id = $id
        RETURN n LIMIT 1
        """
        with driver.session() as s:
            rec = s.run(cypher, id=node_id).single()
            return self._node_view_from(rec["n"]) if rec else None

    def neighbors(
        self, node_id: str, hops: int = 1, edge_types: list[str] | None = None
    ) -> dict[str, Any]:
        driver = self._require()
        hops = max(1, min(hops, 4))
        types_filter = ""
        params: dict[str, Any] = {"id": node_id, "hops": hops}
        if edge_types:
            types_filter = "AND ALL(r IN relationships(p) WHERE type(r) IN $types)"
            params["types"] = [t.upper() for t in edge_types]
        cypher = f"""
        MATCH p = (c)-[*1..{hops}]-(n)
        WHERE (c.game_id = $id OR c.studio_id = $id
               OR c.genre_id = $id OR c.award_id = $id)
              {types_filter}
        RETURN nodes(p) AS ns, relationships(p) AS rs
        LIMIT 500
        """
        with driver.session() as s:
            center = self.get_node(node_id)
            seen_nodes: dict[str, dict[str, Any]] = {}
            edges_out: list[dict[str, str]] = []
            for rec in s.run(cypher, **params):
                for nd in rec["ns"]:
                    v = self._node_view_from(nd)
                    seen_nodes[v["id"]] = v
                for rel in rec["rs"]:
                    edges_out.append(
                        {
                            "source": rel.start_node.get("game_id")
                            or rel.start_node.get("studio_id")
                            or rel.start_node.get("genre_id")
                            or rel.start_node.get("award_id"),
                            "target": rel.end_node.get("game_id")
                            or rel.end_node.get("studio_id")
                            or rel.end_node.get("genre_id")
                            or rel.end_node.get("award_id"),
                            "type": rel.type,
                        }
                    )
            return {
                "center": center,
                "nodes": [seen_nodes[k] for k in seen_nodes if k != node_id],
                "edges": edges_out,
                "hops": hops,
            }

    def shortest_path(self, a: str, b: str) -> dict[str, Any] | None:
        driver = self._require()
        cypher = """
        MATCH (x), (y)
        WHERE (x.game_id = $a OR x.studio_id = $a OR x.genre_id = $a OR x.award_id = $a)
              AND (y.game_id = $b OR y.studio_id = $b OR y.genre_id = $b OR y.award_id = $b)
        MATCH p = shortestPath((x)-[*..8]-(y))
        RETURN nodes(p) AS ns, relationships(p) AS rs
        LIMIT 1
        """
        with driver.session() as s:
            rec = s.run(cypher, a=a, b=b).single()
            if not rec:
                return None
            ns = [self._node_view_from(nd) for nd in rec["ns"]]
            rs = [
                {
                    "source": rel.start_node.get("game_id")
                    or rel.start_node.get("studio_id")
                    or rel.start_node.get("genre_id")
                    or rel.start_node.get("award_id"),
                    "target": rel.end_node.get("game_id")
                    or rel.end_node.get("studio_id")
                    or rel.end_node.get("genre_id")
                    or rel.end_node.get("award_id"),
                    "type": rel.type,
                }
                for rel in rec["rs"]
            ]
            return {"nodes": ns, "edges": rs, "length": len(rs)}

    def stats(self) -> dict[str, int]:
        driver = self._require()
        cypher = """
        MATCH (n) WITH labels(n) AS lbs, count(*) AS c
        RETURN lbs, c
        """
        counts: dict[str, int] = {}
        with driver.session() as s:
            for rec in s.run(cypher):
                for lb in rec["lbs"]:
                    key = {
                        "Game": "games",
                        "Studio": "studios",
                        "Genre": "genres",
                        "Award": "awards",
                    }.get(lb, lb.lower() + "s")
                    counts[key] = counts.get(key, 0) + rec["c"]
        return counts


def get_graph_store(settings) -> GraphStore:
    """按配置选择图后端。选了 neo4j 但连不上时**自动回退**到 networkx。"""
    if getattr(settings, "graph_backend", "networkx") == "neo4j" and getattr(
        settings, "neo4j_uri", ""
    ):
        store = Neo4jStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        if store.is_connected():
            return store
        log.warning("Neo4j 后端不可用，已回退到 networkx 后端")
    return NetworkXStore()
