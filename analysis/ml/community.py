"""社区发现（可插拔）：Louvain / Infomap / Walktrap 三种策略（Strategy + Registry 模式）。

CommunityAnalyzer 按 CommunityConfig.method 选择主方法；detect() 统一返回
``(node2comm, info)``：
  - node2comm: ``{node_id: module_id}``（已按规模重编号，最大簇=0）
  - info: 该方法的「质量」指标
      · Louvain  → ``{"modularity", "n_communities"}``
      · Infomap  → ``{"codelength", "modularity", "n_communities"}``
      · Walktrap → ``{"modularity", "n_communities"}``

新增一种社区发现方法只需写子类并 ``@CommunityDetector.register``，
analyzer 与可视化无需改动——与聚类 Clusterer 保持同一套「可插拔」约定。
"""
from collections import defaultdict

import numpy as np
import networkx as nx


class CommunityDetector:
    name = "base"
    _registry: dict = {}

    def detect(self, G: nx.Graph, cfg) -> tuple:
        """返回 (node2comm, info)。子类实现。"""
        raise NotImplementedError

    @classmethod
    def register(cls, sub):
        cls._registry[sub.name] = sub
        return sub

    @classmethod
    def get(cls, name):
        if name not in cls._registry:
            raise KeyError(f"未知社区发现方法: {name}，可选 {list(cls._registry)}")
        return cls._registry[name]

    @classmethod
    def all(cls):
        return list(cls._registry.values())

    @staticmethod
    def _relabel(node2comm: dict) -> dict:
        """按社区规模重编号，使最大簇为 0，保证报告/配色稳定。"""
        groups = defaultdict(list)
        for n, c in node2comm.items():
            groups[c].append(n)
        order = sorted(groups, key=lambda c: -len(groups[c]))
        remap = {c: i for i, c in enumerate(order)}
        return {n: remap[c] for n, c in node2comm.items()}


def _to_partition(node2comm: dict):
    """{node: module} → [set(module_i) ...]（networkx.community 约定）。"""
    groups = defaultdict(set)
    for n, c in node2comm.items():
        groups[c].add(n)
    return [groups[c] for c in sorted(groups)]


@CommunityDetector.register
class LouvainDetector(CommunityDetector):
    name = "louvain"

    def detect(self, G, cfg):
        comms = nx.community.louvain_communities(
            G, weight="weight", seed=cfg.seed, resolution=cfg.resolution)
        node2comm = {}
        for cid, members in enumerate(comms):
            for m in members:
                node2comm[m] = cid
        node2comm = self._relabel(node2comm)
        q = nx.community.modularity(G, _to_partition(node2comm), weight="weight")
        return node2comm, {"modularity": round(float(q), 4),
                           "n_communities": len(set(node2comm.values()))}


@CommunityDetector.register
class InfomapDetector(CommunityDetector):
    name = "infomap"

    def detect(self, G, cfg):
        import infomap  # lazy：仅在用到 infomap 时才要求该依赖

        # find_communities 直接返回「节点标签集合」，避免内部索引顺序歧义
        comms = infomap.find_communities(G, seed=cfg.seed, num_trials=cfg.num_trials)
        node2comm = {}
        for cid, members in enumerate(comms):
            for m in members:
                node2comm[m] = cid
        node2comm = self._relabel(node2comm)

        # codelength 单独取（Map Equation 的质量指标，越小越好）
        result = infomap.run(G, seed=cfg.seed, num_trials=cfg.num_trials)
        n_mod = int(result.num_top_modules)
        codelength = round(float(result.codelength), 4)

        # 额外用模块度 Q 做同口径对照（Infomap 本身优化的是编码长度而非 Q）
        q = nx.community.modularity(G, _to_partition(node2comm), weight="weight")
        return node2comm, {
            "codelength": codelength,
            "modularity": round(float(q), 4),
            "n_communities": n_mod,
        }


def walktrap_partition(G: nx.Graph, weight: str = "weight", steps: int = 4):
    """随机游走社区发现（Pons & Latapy, 2005）。

    在带权图上做 ``steps`` 步随机游走，用「游走到平稳分布所需的步数」衡量
    两节点距离（ commute / 平稳分布距离），再自底向上合并使该距离最小，
    按模块度 Q 取最优切分。返回 ``(node2comm, Q, n_communities)``。
    """
    nodes = list(G.nodes())
    idx = {n: i for i, n in enumerate(nodes)}
    N = len(nodes)
    A = np.zeros((N, N))
    for u, v, d in G.edges(data=True):
        w = float(d.get(weight, 1.0))
        A[idx[u], idx[v]] = w
        A[idx[v], idx[u]] = w
    deg = A.sum(1)
    vol = deg.sum()
    pi = deg / vol
    pi_safe = pi + 1e-15                       # 度=0 的孤立节点保护
    P = np.zeros((N, N))
    nz = deg > 0
    P[nz] = A[nz] / deg[nz, None]
    Pt = np.linalg.matrix_power(P, steps)
    # 节点间随机游走距离
    r = np.sqrt(((Pt[:, None, :] - Pt[None, :, :]) ** 2 / pi_safe[None, None, :]).sum(2))

    comm = {i: {i} for i in range(N)}
    D = {i: {j: r[i, j] / 2.0 for j in range(N) if j != i} for i in range(N)}
    active = set(range(N))
    best_Q, best_part = -1.0, None

    def modularity(part):
        cset = defaultdict(set)
        for k, v in part.items():
            cset[v].add(k)
        m = vol / 2.0
        Q = 0.0
        for c in cset.values():
            cin = 0.0
            din = 0.0
            for i in c:
                din += deg[i]
                for j in c:
                    cin += A[i, j]
            cin /= 2.0
            Q += cin / m - (din / (2 * m)) ** 2
        return Q

    for _ in range(N - 1):
        bi = bj = None
        best = None
        for i in active:
            for j in active:
                if j <= i:
                    continue
                v = D[i].get(j)
                if v is not None and (best is None or v < best):
                    best = v
                    bi = i
                    bj = j
        new = comm[bi] | comm[bj]
        for k in list(active):
            if k in (bi, bj):
                continue
            s = sum(r[ii, jj] for ii in new for jj in comm[k])
            nd = s / (len(new) + len(comm[k]))
            D[bj][k] = nd
            D[k][bj] = nd
        del D[bi]
        comm[bj] = new
        active.discard(bi)
        part = {}
        for ci, cset in comm.items():
            if ci in active or ci == bj:
                for node in cset:
                    part[node] = ci
        Q = modularity(part)
        if Q > best_Q:
            best_Q = Q
            best_part = {nodes[k]: v for k, v in part.items()}
    return best_part, round(float(best_Q), 4), len(set(best_part.values()))


@CommunityDetector.register
class WalktrapDetector(CommunityDetector):
    name = "walktrap"

    def detect(self, G, cfg):
        node2comm, q, n = walktrap_partition(G, weight="weight", steps=getattr(cfg, "walktrap_steps", 4))
        node2comm = self._relabel(node2comm)
        return node2comm, {"modularity": round(float(q), 4),
                           "n_communities": len(set(node2comm.values()))}
