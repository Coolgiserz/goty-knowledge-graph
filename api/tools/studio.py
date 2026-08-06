"""探索板块：开发商风格相似（双视角并存）。

复用 analysis/ml 的 sp_studio_similarity（最短路径，一阶邻近性）与
rw_game_embeddings（随机游走嵌入，二阶/多跳邻近性）。
参数：视角(sp/rw/both) + 随机游走超参；解读默认 = both + 随机游走默认超参。
"""

import numpy as np
from analysis.ml.analyzers import _mds_coords
from analysis.ml.config import RandomWalkConfig
from analysis.ml.embeddings import rw_game_embeddings
from analysis.ml.similarity import sp_studio_similarity
from scipy.stats import spearmanr
from sklearn.metrics.pairwise import cosine_similarity

from ..graph_loader import G_FULL, GAMES, GG, STUDIO_NAME
from ..models import ParamSpec
from ..registry import ExplorationTool, register


@register
class StudioTool(ExplorationTool):
    name = "studio"
    label = "开发商风格相似（双视角）"
    description = "最短路径（一阶）与随机游走嵌入（多跳）两种视角并存，度量工作室风格接近度。"

    params = [
        ParamSpec(
            "lens",
            "视角",
            "select",
            "both",
            options=["both", "sp", "rw"],
            help="最短路径(sp) / 随机游走嵌入(rw) / 两者(both)",
        ),
        ParamSpec(
            "num_walks",
            "随机游走次数",
            "int",
            25,
            min=5,
            max=60,
            step=5,
            group="随机游走",
            help="每个游戏节点起始的游走次数",
        ),
        ParamSpec(
            "walk_len",
            "游走步数",
            "int",
            40,
            min=10,
            max=80,
            step=10,
            group="随机游走",
            help="每次游走步数",
        ),
        ParamSpec(
            "window",
            "共现窗口",
            "int",
            5,
            min=2,
            max=15,
            step=1,
            group="随机游走",
            help="共现上下文窗口",
        ),
        ParamSpec(
            "embed_dim",
            "嵌入维度",
            "int",
            24,
            min=4,
            max=48,
            step=4,
            group="随机游走",
            help="SVD 嵌入维度",
        ),
    ]
    interpretation_defaults = {
        "lens": "both",
        "num_walks": 25,
        "walk_len": 40,
        "window": 5,
        "embed_dim": 24,
    }
    interpretation = (
        "**默认参数下（双视角 + 随机游走默认超参）的结论**：两种视角下，RPG / 动作 RPG 厂"
        "（贝塞斯达、CD Projekt Red、FromSoftware 等）都明显相近；两视角**距离排序的 Spearman"
        " 相关约 0.80**，说明它们度量的是同一张图上的类型 / 工作室邻近性，只是表示方式不同。\n\n"
        "> 随机游走因 SVD 平滑，相似度数值更分散、分离度更高（Top 对可达 0.9+）；"
        "最短路径更「硬」（仅计跳数，数值压缩在较窄区间）。两者是**同一类信号的不同表示**，"
        "并非彼此独立的「新洞察」。改变随机游走超参或只选单一视角，都会改变数值与排序，"
        "上述具体结论（如某对相似度数值）不再精确——应以图中实际相似度为准。"
    )

    def run(self, params):
        lens = params["lens"]
        sim_sp, studio_ids = sp_studio_similarity(GG, GAMES)

        rw_cfg = RandomWalkConfig(
            num_walks=params["num_walks"],
            walk_len=params["walk_len"],
            window=params["window"],
            embed_dim=params["embed_dim"],
            seed=42,
        )
        emb, gi = rw_game_embeddings(G_FULL, GAMES, rw_cfg)
        from collections import defaultdict

        by_studio = defaultdict(list)
        for n in GAMES:
            gid = n["id"]
            vec = emb[gi[gid]] if gid in gi else np.zeros(emb.shape[1])
            by_studio[n["raw"]["developer_id"]].append(vec)
        S = np.array([np.mean(by_studio[d], axis=0) for d in studio_ids])
        sim_rw = cosine_similarity(S)
        np.fill_diagonal(sim_rw, 1.0)

        names = [STUDIO_NAME.get(s, s) for s in studio_ids]
        panels, tables = [], []

        def _heat(sim, suffix):
            return {
                "type": "heatmap",
                "title": f"工作室风格相似度（{suffix}）",
                "data": {
                    "labels": names,
                    "matrix": [
                        [round(float(sim[i, j]), 3) for j in range(len(names))]
                        for i in range(len(names))
                    ],
                    "lowColor": "#1f232c",
                    "highColor": "#f5b301",
                },
            }

        def _scatter(sim, suffix):
            coords = _mds_coords(sim, random_state=42)
            points = [
                [round(float(coords[i, 0]), 3), round(float(coords[i, 1]), 3), names[i]]
                for i in range(len(names))
            ]
            return {
                "type": "scatter",
                "title": f"工作室风格空间（{suffix}，MDS）",
                "data": {
                    "series": [{"name": suffix, "color": "#3b6ea5", "points": points}],
                    "caption": "点越近 ≈ 风格越接近",
                },
            }

        def _top_pairs(sim):
            pairs = []
            n = len(studio_ids)
            for i in range(n):
                for j in range(i + 1, n):
                    pairs.append((names[i], names[j], round(float(sim[i, j]), 3)))
            pairs.sort(key=lambda x: -x[2])
            return pairs[:8]

        if lens in ("both", "sp"):
            panels.append(_heat(sim_sp, "最短路径"))
            panels.append(_scatter(sim_sp, "最短路径"))
            tables.append(
                {
                    "title": "最相似工作室对（最短路径，Top8）",
                    "columns": ["工作室 A", "工作室 B", "相似度"],
                    "rows": [[a, b, s] for a, b, s in _top_pairs(sim_sp)],
                }
            )
        if lens in ("both", "rw"):
            panels.append(_heat(sim_rw, "随机游走"))
            panels.append(_scatter(sim_rw, "随机游走"))
            tables.append(
                {
                    "title": "最相似工作室对（随机游走，Top8）",
                    "columns": ["工作室 A", "工作室 B", "余弦相似度"],
                    "rows": [[a, b, s] for a, b, s in _top_pairs(sim_rw)],
                }
            )

        tables.append(
            {
                "title": "工作室列表",
                "columns": ["工作室", "作品数"],
                "rows": [[names[i], len(by_studio[studio_ids[i]])] for i in range(len(studio_ids))],
            }
        )

        metrics = {"n_studios": len(studio_ids)}
        if lens == "both":
            off = ~np.eye(len(studio_ids), dtype=bool)
            rho, _ = spearmanr((1.0 - sim_sp)[off], (1.0 - sim_rw)[off])
            metrics["spearman_rho"] = round(float(rho), 3)
        return {"panels": panels, "tables": tables, "metrics": metrics}
