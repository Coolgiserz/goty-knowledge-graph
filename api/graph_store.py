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

工厂 :func:`get_graph_store` 按配置选择后端；若显式选了 neo4j 但**初始化阶段**连不上，
会在启动即回退到 networkx 并打告警（这是诚实的“起不来就换底座”，不影响已连上的查询）。
一旦连上 Neo4j，查询就走真实 Cypher；查询过程中**不再静默回退**——出现异常会如实抛出，
由路由层转成 503/500，错误必须可见、可定位，而不是被悄悄换成内存数据糊弄过去。

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

import networkx as nx

from .community import run_detection
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

    @abstractmethod
    def list_nodes(
        self,
        group: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """浏览节点：按 group 过滤 + 关键词检索 + 分页，返回节点视图列表（供表格）。"""

    @abstractmethod
    def seed(self, group: str | None = None, limit: int = 12, hops: int = 1) -> dict[str, Any]:
        """按 group 取一批「种子」节点并向外展开 ``hops`` 跳，返回合并子图（供初始画布）。"""

    @abstractmethod
    def filter(
        self,
        group: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        hops: int = 1,
    ) -> dict[str, Any]:
        """按类别(group)+标签(tags, 类型名称)筛选节点并向外展开 ``hops`` 跳。

        「渲染种子」用：用户按类别（游戏/工作室/类型/奖项）与标签（genre 名称，
        如 开放世界、角色扮演）挑一批节点作为探索起点。``tags`` 仅在游戏类目下生效，
        表示「属于这些类型的游戏」。"""

    @abstractmethod
    def communities(
        self,
        algorithm: str = "modularity",
        params: dict[str, Any] | None = None,
        animate: bool = False,
    ) -> dict[str, Any]:
        """社区发现：返回社团汇总（规模 + 成员）与每个节点的社团归属，供整图上色可视化。

        ``params`` 为算法特有参数（如 ``modularity`` 的 ``resolution`` 粒度控制）；
        不同算法忽略不相关参数。``animate=True`` 时额外返回 ``frames``（过程快照，
        供前端逐步重着色做教育性动画）。返回结构含 ``algorithm``、``params``（实际生效参数）、
        ``communities``（按规模降序，每项为 ``{id,size,members}``）、``nodes``、``edges``、
        ``supports_animation``、``modularity``、``frames``。
        """

    @abstractmethod
    def influence(
        self, metric: str = "pagerank", top_n: int = 20, group: str | None = None
    ) -> dict[str, Any]:
        """节点影响力（中心性）排行榜：揭示「谁在图谱里最重要」。

        - ``metric``：``degree``（度数中心性）/ ``pagerank``（默认）/ ``betweenness``（中介）。
        - ``top_n``：返回前 N 名。
        - ``group``：按节点类型过滤（game/goty/studio/genre/award），``None`` 表示全类型。
        返回 ``{metric, top_n, group, results:[{id,label,group,score}]}``（已按分数降序）。
        """


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

    def list_nodes(
        self,
        group: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        q = (query or "").strip().lower()
        matched: list[dict[str, Any]] = []
        for n in NODES:
            if group and n.get("group") != group:
                continue
            if q:
                haystack = " ".join(
                    str(v) for v in (n.get("label", ""), n.get("raw") or {})
                ).lower()
                if q not in haystack:
                    continue
            matched.append(n)
        total = len(matched)
        page = matched[offset : offset + limit]
        return {
            "group": group,
            "query": query or "",
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": [_node_view(x) for x in page],
        }

    def _expand(self, cands, hops):
        """把一批中心节点向外无向展开 ``hops`` 跳，返回 (nodes_out, edges_out)。"""
        hops = max(0, min(hops, 4))
        visited: set[str] = set()
        nodes_out: list[dict[str, Any]] = []
        edges_out: list[dict[str, str]] = []
        for c in cands:
            cid = c["id"]
            local_visited = {cid}
            frontier = [cid]
            for _ in range(hops):
                nxt: list[str] = []
                for u in frontier:
                    for v, ty in self._adj.get(u, []):
                        edges_out.append({"source": u, "target": v, "type": ty})
                        if v not in local_visited:
                            local_visited.add(v)
                            nxt.append(v)
                frontier = nxt
                if not frontier:
                    break
            for nid in local_visited:
                if nid not in visited:
                    visited.add(nid)
                    nodes_out.append(self._nodes[nid])
        return nodes_out, edges_out

    def seed(self, group: str | None = None, limit: int = 12, hops: int = 1) -> dict[str, Any]:
        """按 group 取一批「种子」节点并向外展开 ``hops`` 跳，返回合并子图（供初始画布）。"""
        hops = max(1, min(hops, 4))
        cands = [n for n in NODES if (not group or n.get("group") == group)]
        cands = cands[: max(1, limit)]
        nodes_out, edges_out = self._expand(cands, hops)
        return {
            "center": None,
            "group": group,
            "nodes": [_node_view(n) for n in nodes_out],
            "edges": edges_out,
            "hops": hops,
        }

    def filter(
        self,
        group: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        hops: int = 1,
    ) -> dict[str, Any]:
        """按类别(group)+标签(tags, 类型名称)筛选节点并向外展开 ``hops`` 跳。

        「渲染种子」用：用户按类别（游戏/工作室/类型/奖项）与标签（genre 名称，
        如 开放世界、角色扮演）挑一批节点作为探索起点。``tags`` 仅在游戏类目下生效，
        表示「属于这些类型的游戏」。"""
        limit = max(1, min(limit, 200))
        hops = max(0, min(hops, 4))
        tags = [t.strip() for t in (tags or []) if t.strip()]
        if tags:
            tag_lower = [t.lower() for t in tags]
            genre_ids = {
                n["id"]
                for n in NODES
                if n.get("group") == "genre"
                and any(t in (n.get("label") or "").lower() for t in tag_lower)
            }
            matched: list[dict[str, Any]] = []
            for n in NODES:
                g = n.get("group")
                if g not in ("game", "goty"):
                    continue
                if group == "goty" and g != "goty":
                    continue
                if group == "game" and g != "game":
                    continue
                genres_of = {
                    v for (v, ty) in self._adj.get(n["id"], []) if ty == "BELONGS_TO_GENRE"
                }
                if genres_of & genre_ids:
                    matched.append(n)
        else:
            matched = [n for n in NODES if (not group or n.get("group") == group)]
        matched = matched[:limit]
        nodes_out, edges_out = self._expand(matched, hops)
        return {
            "center": None,
            "group": group,
            "tags": tags,
            "nodes": [_node_view(n) for n in nodes_out],
            "edges": edges_out,
            "hops": hops,
        }

    def communities(
        self,
        algorithm: str = "modularity",
        params: dict[str, Any] | None = None,
        animate: bool = False,
    ) -> dict[str, Any]:
        G = nx.Graph()
        G.add_nodes_from(self._nodes.keys())
        seen: set[frozenset] = set()
        edges_out: list[dict[str, str]] = []
        for u in self._adj:
            for v, ty in self._adj[u]:
                key = frozenset((u, v))
                if key in seen:
                    continue
                seen.add(key)
                G.add_edge(u, v)
                edges_out.append({"source": u, "target": v, "type": ty})
        res = run_detection(algorithm, G, params, animate=animate)
        nodes_out = []
        for nid, n in self._nodes.items():
            view = _node_view(n)
            view["community"] = res.assignment[nid]
            nodes_out.append(view)
        return {
            "algorithm": res.algorithm,
            "display_name": res.display_name,
            "params": res.params,
            "communities": res.communities,
            "nodes": nodes_out,
            "edges": edges_out,
            "supports_animation": res.supports_animation,
            "modularity": res.modularity,
            "frames": res.frames if animate else [],
        }

    def influence(
        self, metric: str = "pagerank", top_n: int = 20, group: str | None = None
    ) -> dict[str, Any]:
        G = nx.Graph()
        G.add_nodes_from(self._nodes.keys())
        seen: set[frozenset] = set()
        for u in self._adj:
            for v, _ty in self._adj[u]:
                key = frozenset((u, v))
                if key in seen:
                    continue
                seen.add(key)
                G.add_edge(u, v)
        if G.number_of_nodes() == 0:
            return {"metric": metric, "top_n": top_n, "group": group, "results": []}
        scores = _compute_influence(G, metric)
        node_views = {nid: _node_view(n) for nid, n in self._nodes.items()}
        results = _influence_results(node_views, scores, top_n, group)
        return {"metric": metric, "top_n": top_n, "group": group, "results": results}


class Neo4jStore(GraphStore):
    """可选后端：经官方 driver 用 Cypher 查询 Neo4j。

    连接为**惰性**：构造时尝试连接，失败仅打告警并把 ``connected`` 置 False，
    不抛异常（配合 :func:`get_graph_store` 的初始化阶段回退策略）。所有查询方法在
    未连接时抛 ``RuntimeError("neo4j_unavailable")``，由路由层转成 503。
    一旦连上，查询就走真实 Cypher；查询中若抛异常，**如实抛出**（不静默回退）。
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
        # 走到 Neo4jStore 且初始化连上，即如实标识为 neo4j（不再中途静默切换）。
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

    def list_nodes(
        self,
        group: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        driver = self._require()
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        q = (query or "").strip().lower()
        pred = _group_predicate(group)
        where = pred
        if q:
            qpred = (
                "toLower(coalesce(n.title_zh,'')) CONTAINS $q OR "
                "toLower(coalesce(n.title,'')) CONTAINS $q OR "
                "toLower(coalesce(n.name,'')) CONTAINS $q OR "
                "toLower(coalesce(n.name_zh,'')) CONTAINS $q OR "
                "toLower(coalesce(n.developer,'')) CONTAINS $q OR "
                "toLower(coalesce(n.body,'')) CONTAINS $q OR "
                "toLower(coalesce(n.label,'')) CONTAINS $q"
            )
            where = (where + " AND " + qpred) if where else qpred
        base = "MATCH (n)" + (f" WHERE {where}" if where else "")
        with driver.session() as s:
            items = [
                self._node_view_from(rec["n"])
                for rec in s.run(
                    base + " RETURN n ORDER BY n.id SKIP $offset LIMIT $limit",
                    offset=offset,
                    limit=limit,
                    **({"q": q} if q else {}),
                )
            ]
            total = s.run(base + " RETURN count(n) AS c", **({"q": q} if q else {})).single()["c"]
        return {
            "group": group,
            "query": query or "",
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    def seed(self, group: str | None = None, limit: int = 12, hops: int = 1) -> dict[str, Any]:
        driver = self._require()
        limit = max(1, min(limit, 100))
        hops = max(1, min(hops, 4))
        pred = _group_predicate(group)
        match = "MATCH (n)" + (f" WHERE {pred}" if pred else "")
        with driver.session() as s:
            seed_ids = [
                self._node_view_from(rec["n"])["id"]
                for rec in s.run(match + " RETURN n LIMIT $limit", limit=limit)
            ]
        if not seed_ids:
            return {"center": None, "group": group, "nodes": [], "edges": [], "hops": hops}
        cypher = f"""
        UNWIND $seedIds AS sid
        MATCH (s) WHERE s.game_id = sid OR s.studio_id = sid
                       OR s.genre_id = sid OR s.award_id = sid
        MATCH p = (s)-[*1..{hops}]-(m)
        RETURN nodes(p) AS ns, relationships(p) AS rs
        LIMIT 2000
        """
        seen_nodes: dict[str, dict[str, Any]] = {}
        edges_out: list[dict[str, str]] = []
        with driver.session() as s:
            for rec in s.run(cypher, seedIds=seed_ids):
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
            "center": None,
            "group": group,
            "nodes": list(seen_nodes.values()),
            "edges": edges_out,
            "hops": hops,
        }

    def filter(
        self,
        group: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        hops: int = 1,
    ) -> dict[str, Any]:
        """按类别 + 标签筛选（语义同 NetworkXStore.filter；见其注释）。"""
        driver = self._require()
        limit = max(1, min(limit, 200))
        hops = max(0, min(hops, 4))
        tags = [t.strip() for t in (tags or []) if t.strip()]
        with driver.session() as s:
            if tags:
                matched: list[dict[str, Any]] = []
                for rec in s.run(
                    "UNWIND $tags AS tg "
                    "MATCH (g:Genre) WHERE toLower(g.name) CONTAINS toLower(tg) "
                    "MATCH (gm:Game)-[:BELONGS_TO_GENRE]->(g) "
                    "RETURN DISTINCT gm",
                    tags=tags,
                ):
                    v = self._node_view_from(rec["gm"])
                    if group == "goty" and v.get("group") != "goty":
                        continue
                    if group == "game" and v.get("group") != "game":
                        continue
                    matched.append(v)
                seed_ids = [v["id"] for v in matched[:limit]]
            else:
                pred = _group_predicate(group)
                match = "MATCH (n)" + (f" WHERE {pred}" if pred else "")
                seed_ids = [
                    self._node_view_from(rec["n"])["id"]
                    for rec in s.run(match + " RETURN n LIMIT $limit", limit=limit)
                ]
        if not seed_ids:
            return {
                "center": None,
                "group": group,
                "tags": tags,
                "nodes": [],
                "edges": [],
                "hops": hops,
            }
        if hops == 0:
            # hops=0：仅返回筛选命中的节点，不做邻居展开（与 networkx 后端语义一致）
            seen_nodes: dict[str, dict[str, Any]] = {}
            with driver.session() as s:
                for rec in s.run(
                    "UNWIND $seedIds AS sid "
                    "MATCH (n) WHERE n.game_id = sid OR n.studio_id = sid "
                    "              OR n.genre_id = sid OR n.award_id = sid "
                    "RETURN DISTINCT n",
                    seedIds=seed_ids,
                ):
                    v = self._node_view_from(rec["n"])
                    seen_nodes[v["id"]] = v
            return {
                "center": None,
                "group": group,
                "tags": tags,
                "nodes": list(seen_nodes.values()),
                "edges": [],
                "hops": 0,
            }
        cypher = f"""
        UNWIND $seedIds AS sid
        MATCH (s) WHERE s.game_id = sid OR s.studio_id = sid
                       OR s.genre_id = sid OR s.award_id = sid
        MATCH p = (s)-[*1..{hops}]-(m)
        RETURN nodes(p) AS ns, relationships(p) AS rs
        LIMIT 4000
        """
        seen_nodes: dict[str, dict[str, Any]] = {}
        edges_out: list[dict[str, str]] = []
        with driver.session() as s:
            for rec in s.run(cypher, seedIds=seed_ids):
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
            "center": None,
            "group": group,
            "tags": tags,
            "nodes": list(seen_nodes.values()),
            "edges": edges_out,
            "hops": hops,
        }

    def communities(
        self,
        algorithm: str = "modularity",
        params: dict[str, Any] | None = None,
        animate: bool = False,
    ) -> dict[str, Any]:
        driver = self._require()
        # 拉全图：节点视图（含 id）+ 无向边
        node_views: dict[str, dict[str, Any]] = {}
        with driver.session() as s:
            for rec in s.run("MATCH (n) RETURN n LIMIT 10000"):
                v = self._node_view_from(rec["n"])
                node_views[v["id"]] = v

        G = nx.Graph()
        G.add_nodes_from(node_views.keys())
        seen: set[frozenset] = set()
        edges_out: list[dict[str, str]] = []
        with driver.session() as s:
            for rec in s.run("MATCH (a)-[r]-(b) RETURN a, b LIMIT 20000"):
                a = self._node_view_from(rec["a"])["id"]
                b = self._node_view_from(rec["b"])["id"]
                G.add_edge(a, b)
                key = frozenset((a, b))
                if key in seen:
                    continue
                seen.add(key)
                edges_out.append({"source": a, "target": b, "type": rec["r"].type})
        res = run_detection(algorithm, G, params, animate=animate)
        nodes_out = []
        for nid, v in node_views.items():
            view = dict(v)
            view["community"] = res.assignment[nid]
            nodes_out.append(view)
        return {
            "algorithm": res.algorithm,
            "display_name": res.display_name,
            "params": res.params,
            "communities": res.communities,
            "nodes": nodes_out,
            "edges": edges_out,
            "supports_animation": res.supports_animation,
            "modularity": res.modularity,
            "frames": res.frames if animate else [],
        }

    def influence(
        self, metric: str = "pagerank", top_n: int = 20, group: str | None = None
    ) -> dict[str, Any]:
        driver = self._require()
        node_views: dict[str, dict[str, Any]] = {}
        with driver.session() as s:
            for rec in s.run("MATCH (n) RETURN n LIMIT 10000"):
                v = self._node_view_from(rec["n"])
                node_views[v["id"]] = v
        G = nx.Graph()
        G.add_nodes_from(node_views.keys())
        seen: set[frozenset] = set()
        with driver.session() as s:
            for rec in s.run("MATCH (a)-[r]-(b) RETURN a, b LIMIT 20000"):
                a = self._node_view_from(rec["a"])["id"]
                b = self._node_view_from(rec["b"])["id"]
                key = frozenset((a, b))
                if key in seen:
                    continue
                seen.add(key)
                G.add_edge(a, b)
        if G.number_of_nodes() == 0:
            return {"metric": metric, "top_n": top_n, "group": group, "results": []}
        scores = _compute_influence(G, metric)
        results = _influence_results(node_views, scores, top_n, group)
        return {"metric": metric, "top_n": top_n, "group": group, "results": results}


def _group_predicate(group: str | None) -> str:
    """把前端 group 名翻译成 Cypher 的标签/属性谓词（不含 WHERE 关键字）。"""
    if group == "goty":
        return "n:Game AND n.is_goty = true"
    if group == "game":
        return "n:Game AND (n.is_goty IS NULL OR n.is_goty = false)"
    if group == "studio":
        return "n:Studio"
    if group == "genre":
        return "n:Genre"
    if group == "award":
        return "n:Award"
    return ""


def _compute_influence(G, metric: str) -> dict[str, float]:
    """在给定无向图 G 上计算节点影响力（中心性），返回 {node_id: score}。

    三种指标各有教育意义，覆盖「谁在图谱里最重要」的不同侧面：

    - ``degree``（度数中心性）：直接相连的节点越多越重要。最直观——
      如最多产的工作室、涵盖游戏最多的类型。
    - ``pagerank``（PageRank，默认）：不仅看连接数量，还看连接对象的“重要性”
      （被大奖/大厂环绕的节点得分更高），更能反映真实话语权。
    - ``betweenness``（中介中心性）：位于不同群体之间的“桥梁”节点；
      去掉它，很多社团将彼此失联——揭示图谱的结构性枢纽。
    """
    if metric == "degree":
        return nx.degree_centrality(G)
    if metric == "betweenness":
        return nx.betweenness_centrality(G, normalized=True)
    # 默认 pagerank
    return nx.pagerank(G, alpha=0.85)


def _influence_results(
    node_views: dict[str, dict[str, Any]], scores: dict[str, float], top_n: int, group: str | None
) -> list[dict[str, Any]]:
    """把中心性分数转成可排序、可截断、可按类型过滤的排行榜条目。"""
    rows = []
    for nid, view in node_views.items():
        if group and view.get("group") != group:
            continue
        rows.append(
            {
                "id": nid,
                "label": view.get("label") or nid,
                "group": view.get("group") or "",
                "score": float(scores.get(nid, 0.0)),
            }
        )
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows[:top_n]


def get_graph_store(settings) -> GraphStore:
    """按配置选择图后端。

    - 未选 neo4j → 直接内存 networkx（默认、零依赖、离线可用）。
    - 选了 neo4j 且初始化连得上 → 用 Neo4jStore（真实 Cypher，backend="neo4j"）。
    - 选了 neo4j 但初始化连不上 → 在**启动阶段**回退 networkx 并告警，返回 NetworkXStore。
      注意：这里只做“起不来就换底座”的初始化回退；连上之后若查询仍出错，会如实抛出
      （由路由转 503/500），不再中途静默切换，保证错误可见、可定位。
    """
    if getattr(settings, "graph_backend", "networkx") == "neo4j" and getattr(
        settings, "neo4j_uri", ""
    ):
        store = Neo4jStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        if store.is_connected():
            return store
        log.warning("Neo4j 后端初始化不可用，已回退到 networkx 后端")
    return NetworkXStore()
