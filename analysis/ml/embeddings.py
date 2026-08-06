"""异构随机游走嵌入（游戏节点低维向量）。

在完整异构图（游戏↔类型↔工作室↔奖项）上跑截断随机游走，统计游戏节点共现
→ 共现矩阵(log1p) → TruncatedSVD 降维，得到「游戏嵌入」。

这是 StudioStyleAnalyzer 与 API 探索板块共用的可复用计算模块：
随机游走捕捉二阶 / 多跳邻近性（经由共享类型 / 工作室间接相连），
比直接共享类型（最短路径）更平滑，是「工作室风格空间」的一种视角。
"""
from collections import defaultdict

import numpy as np
from sklearn.decomposition import TruncatedSVD

from .config import RandomWalkConfig


def rw_game_embeddings(Gfull, games, cfg: RandomWalkConfig = None):
    """在 G_full 上做截断随机游走，得到游戏节点的低维嵌入。

    参数
    ----
    Gfull : nx.Graph  完整异构图（含游戏 / 类型 / 工作室 / 奖项节点）
    games : list       游戏节点字典列表（与 graph.json 同结构，含 "id" / "raw"）
    cfg   : RandomWalkConfig  含 num_walks / walk_len / window / embed_dim / seed

    返回
    ----
    (emb, gi)：emb 是 N×dim 矩阵（按共现中出现顺序），gi 是 game_id→行号 字典。
    只在「游戏」节点上累积共现（类型 / 工作室 / 奖项节点作为桥接）。
    """
    if cfg is None:
        cfg = RandomWalkConfig()
    gids = [n["id"] for n in games]
    gindex = {gid: i for i, gid in enumerate(gids)}
    rng = np.random.default_rng(cfg.seed)
    neighbors = {n: list(Gfull.neighbors(n)) for n in Gfull.nodes()}
    co = defaultdict(lambda: defaultdict(int))
    for start in gids:
        for _ in range(cfg.num_walks):
            cur = start
            walk = [cur]
            for _ in range(cfg.walk_len):
                nbrs = neighbors[cur]
                if not nbrs:
                    break
                cur = nbrs[rng.integers(len(nbrs))]
                walk.append(cur)
            WIN = cfg.window
            for a in range(len(walk)):
                if walk[a] not in gindex:
                    continue
                for b in range(max(0, a - WIN), min(len(walk), a + WIN + 1)):
                    if b == a or walk[b] not in gindex:
                        continue
                    co[walk[a]][walk[b]] += 1
    game_ids = list(co.keys())
    gi = {gid: i for i, gid in enumerate(game_ids)}
    M = np.zeros((len(game_ids), len(game_ids)))
    for a, d in co.items():
        for b, c in d.items():
            M[gi[a], gi[b]] = c
    M = M + M.T
    emb = TruncatedSVD(n_components=cfg.embed_dim, random_state=cfg.seed
                       ).fit_transform(np.log1p(M))
    return emb, gi
