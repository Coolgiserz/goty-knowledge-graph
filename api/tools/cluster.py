"""探索板块：聚类（因子画像）。

复用 analysis/ml 的 FeatureEngine（特征工程）、Clusterer（聚类策略）与
ClusterAnalyzer._profile（簇画像）。参数：算法 / 固定 k / PCA / 标准化 / 是否含工作室夺冠数。
解读默认 = kmeans + PCA + 含 studio_wins + 自动选 k。
"""

from collections import Counter

from analysis.ml.analyzers import ClusterAnalyzer
from analysis.ml.clusterers import Clusterer
from analysis.ml.config import MLConfig
from analysis.ml.context import PipelineContext
from analysis.ml.features import FeatureEngine
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from ..models import ParamSpec
from ..registry import ExplorationTool, register

_IDENT = {"game_id", "title", "title_zh", "developer_id", "developer", "is_goty"}
_PALETTE = [
    "#f5b301",
    "#3b6ea5",
    "#27ae60",
    "#8e44ad",
    "#e74c3c",
    "#16a085",
    "#d35400",
    "#7f8c8d",
    "#2980b9",
    "#c0392b",
]


@register
class ClusterTool(ExplorationTool):
    name = "cluster"
    label = "聚类（因子画像）"
    description = "在标准化因子矩阵上聚类，看游戏如何按数值特征（评分/年份/类型/声誉）分群。"

    params = [
        ParamSpec(
            "method",
            "聚类算法",
            "select",
            "kmeans",
            options=["kmeans", "hierarchical", "spectral", "dbscan"],
            help="kmeans / 层次 / 谱 / DBSCAN",
        ),
        ParamSpec(
            "k",
            "固定 k（0=自动选）",
            "int",
            0,
            min=0,
            max=12,
            step=1,
            help="0 时按轮廓系数在 2..9 选优；非 0 直接定 k",
        ),
        ParamSpec(
            "use_pca",
            "PCA 预处理",
            "bool",
            True,
            help="PCA 白化后再聚类，缓解高维 one-hot 维度灾难",
        ),
        ParamSpec(
            "scale", "标准化", "bool", True, help="StandardScaler 标准化（关掉可看原始尺度）"
        ),
        ParamSpec(
            "include_studio_wins",
            "含工作室夺冠数",
            "bool",
            True,
            help="关闭可消除 is_goty 标签泄漏（结果更纯玩法）",
        ),
    ]
    interpretation_defaults = {
        "method": "kmeans",
        "use_pca": True,
        "include_studio_wins": True,
        "k": 0,
    }
    interpretation = (
        "**默认参数（kmeans + PCA + 含 studio_wins + 自动选 k）下的结论**：游戏在因子空间里聚成若干簇，"
        "每簇有主导玩法类型与评分特征。但聚类轮廓系数通常偏低（<0.25），说明游戏在因子空间呈连续谱，"
        "k 只是**探索性划分**，并非严谨类别边界。\n\n"
        "> 关闭「含工作室夺冠数」会改变簇的构成（去掉 is_goty 标签泄漏）；"
        "切换算法 / 固定 k / 关闭 PCA 都会产生不同的簇结构与数量，"
        "上述「每簇代表什么」的定性描述随之改变——请以实际簇画像表为准。"
    )

    def run(self, params):
        cfg = MLConfig()
        cfg.cluster.method = params["method"]
        cfg.cluster.use_pca = params["use_pca"]
        cfg.cluster.scale = params["scale"]
        cfg.cluster.fixed_k = params["k"] if params["k"] > 0 else None
        cfg.features.include_studio_wins = params["include_studio_wins"]

        ctx = PipelineContext(cfg)
        df, _ = FeatureEngine(cfg).extract(ctx)

        cols = [c for c in df.columns if c not in _IDENT]
        X = df[cols].copy()
        if X["player_rating"].isna().any():
            X["player_rating"] = X["player_rating"].fillna(X["player_rating"].median())
        Xs = (
            StandardScaler().fit_transform(X.values)
            if cfg.cluster.scale
            else X.values.astype(float)
        )

        coords2d = PCA(n_components=2, random_state=cfg.random_state).fit_transform(Xs)
        cluster_X = (
            PCA(n_components=cfg.cluster.pca_variance, random_state=cfg.random_state).fit_transform(
                Xs
            )
            if cfg.cluster.use_pca
            else Xs
        )

        method = Clusterer.get(cfg.cluster.method)(cfg)
        scores, best_k = {}, None
        if cfg.cluster.fixed_k is not None:
            best_k = cfg.cluster.fixed_k
        elif method.needs_k():
            lo, hi = cfg.cluster.k_range
            for k in range(lo, hi + 1):
                lab = method.fit_predict(cluster_X, k)
                if len(set(lab)) > 1:
                    scores[k] = float(silhouette_score(cluster_X, lab))
            best_k = max(scores, key=scores.get) if scores else None
        else:
            best_k = None

        if best_k is not None:
            labels = method.fit_predict(cluster_X, best_k)
        else:
            labels = method.fit_predict(cluster_X, None)
            best_k = int(len(set(labels)) - (1 if -1 in labels else 0))

        out = df.copy()
        out["cluster"] = labels
        profiles = ClusterAnalyzer._profile(out, labels, ctx.genre_names)

        points = [
            [
                round(float(coords2d[i, 0]), 3),
                round(float(coords2d[i, 1]), 3),
                str(df.iloc[i]["title_zh"]),
                int(labels[i]),
            ]
            for i in range(len(df))
        ]
        series = []
        for c in sorted(set(labels)):
            cp = [[p[0], p[1], p[2]] for p in points if p[3] == c]
            color = "#555555" if c < 0 else _PALETTE[int(c) % len(_PALETTE)]
            series.append({"name": f"簇{c}", "color": color, "points": cp})
        scatter = {
            "type": "scatter",
            "title": f"聚类散点（{params['method']}"
            f"{'+PCA' if cfg.cluster.use_pca else ''}，k={best_k}）",
            "data": {"series": series, "caption": "颜色=簇；点=游戏（PCA 2D）"},
        }

        sizes = Counter(int(lab) for lab in labels)
        bar = {
            "type": "bar",
            "title": "各簇规模",
            "data": {
                "categories": [f"簇{c}" for c in sorted(sizes)],
                "series": [
                    {
                        "name": "规模",
                        "color": "#f5b301",
                        "values": [int(sizes[c]) for c in sorted(sizes)],
                    }
                ],
                "horizontal": False,
            },
        }

        table = {
            "title": "簇画像（按规模）",
            "columns": ["簇", "规模", "年度最佳", "GOTY率", "均分", "代表游戏", "主导类型"],
            "rows": [
                [
                    p["cluster"],
                    p["size"],
                    p["goty"],
                    p["goty_rate"],
                    p["avg_rating"],
                    "、".join(p["top_games"][:5]),
                    "、".join(f"{g}({v})" for g, v in p["top_genres"][:4]),
                ]
                for p in profiles
            ],
        }

        metrics = {
            "method": params["method"],
            "best_k": int(best_k),
            "use_pca": cfg.cluster.use_pca,
            "silhouette": {int(k): round(v, 3) for k, v in scores.items()},
        }
        return {"panels": [scatter, bar], "tables": [table], "metrics": metrics}
