"""开发商风格相似度（最短路径，一阶邻近性）。

基于游戏-游戏相似投影图 GG 的最短路径距离，定义工作室间相似度——
捕捉「直接共享类型 / 同工作室」的一阶邻近性（只数跳数）。

这是 StudioStyleAnalyzer 与 API 探索板块共用的可复用计算模块，
与随机游走嵌入并存、互为对照（两者度量同一类信号的不同表示）。
"""
from collections import defaultdict

import numpy as np
import networkx as nx


def sp_studio_similarity(GG, games):
    """基于 GG 的最短路径距离，定义工作室间相似度。

    边距离 = 1/(1+边权)；工作室距离 = 双向平均最近邻图距离；
    相似度 = 1/(1+距离)，连续有界。

    返回 (sim 矩阵, studio_ids)。
    """
    DG = nx.Graph()
    for u, v, d in GG.edges(data=True):
        DG.add_edge(u, v, weight=1.0 / (1.0 + d["weight"]))
    try:
        apsp = dict(nx.all_pairs_shortest_path_length(DG))
    except Exception:
        apsp = {n: {n: 0.0} for n in DG.nodes()}
    INF = 10.0
    by_studio = defaultdict(list)
    for n in games:
        by_studio[n["raw"]["developer_id"]].append(n["id"])
    studio_ids = sorted(by_studio.keys())
    n = len(studio_ids)
    D = np.zeros((n, n))

    def _mindist(a, gj):
        da = apsp.get(a, {})
        return min((da.get(b, INF) for b in gj), default=INF)

    for i, si in enumerate(studio_ids):
        gi = by_studio[si]
        for j, sj in enumerate(studio_ids):
            if i == j:
                continue
            gj = by_studio[sj]
            d_ij = np.mean([_mindist(a, gj) for a in gi])
            d_ji = np.mean([_mindist(b, gi) for b in gj])
            D[i, j] = 0.5 * (d_ij + d_ji)
    np.fill_diagonal(D, 0.0)
    sim = 1.0 / (1.0 + D)
    np.fill_diagonal(sim, 1.0)
    return sim, studio_ids
