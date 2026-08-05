"""特征工程（高频因子）：可组合、可注册的特征组。

设计：每个特征组是一个 FeatureGroup 子类，通过 @FeatureGroup.register 注册。
FeatureEngine 按配置中的 groups 顺序实例化并提取，产出一个宽因子表(DataFrame) +
因子文档(供报告/可视化)。

为什么拆成组（而非一个大函数）：
  - 可插拔：新增特征只需写一个子类并注册，不改引擎。
  - 可解释：每个组自带 columns() 与 doc()，因子含义一目了然。
  - 可控特征选择：在 FeatureConfig.groups 里增删即可，无需改聚类代码。

关于特征选择的已知问题（已在报告中说明）：
  - 拓扑因子(pr/pagerank/betweenness/clustering)由「玩法相似图」派生，
    而类型 one-hot 也刻画同一张图 —— 二者对用户相似度信号有重叠（冗余）。
    开启 PCA 预处理可缓解；亦可只保留其中一组。
  - studio_wins 由 is_goty 派生，属于「标签泄漏」：把它当聚类特征会让簇偏向工作室。
    故默认保留但显式标注，可通过 FeatureConfig.include_studio_wins=False 关闭。
"""
import numpy as np
import pandas as pd
import networkx as nx
from collections import defaultdict

from .context import PipelineContext
from .io_utils import build_full_nx


class FeatureGroup:
    """特征组基类。子类需定义 name、columns、contribute；可选 prepare。"""
    name = "base"
    _registry: dict = {}

    def __init__(self, config):
        self.config = config

    def prepare(self, ctx: PipelineContext):
        """可选：在遍历节点前做一次性全局计算。"""

    def columns(self, ctx: PipelineContext) -> list:
        raise NotImplementedError

    def contribute(self, ctx: PipelineContext, node: dict, row: dict):
        raise NotImplementedError

    def doc(self, ctx: PipelineContext) -> list:
        return []

    @classmethod
    def register(cls, sub):
        cls._registry[sub.name] = sub
        return sub


# --------------------------------------------------------------------------
# 图拓扑因子：在「游戏-游戏相似投影图」上的中心性
# --------------------------------------------------------------------------
@FeatureGroup.register
class TopologyFeatures(FeatureGroup):
    name = "topology"

    def prepare(self, ctx: PipelineContext):
        GG = ctx.GG
        ctx._topo = {
            "pr": nx.pagerank(GG, alpha=0.85),
            "bc": nx.betweenness_centrality(GG),
            "cl": nx.clustering(GG),
            "deg": dict(GG.degree()),
        }

    def columns(self, ctx: PipelineContext) -> list:
        return ["gg_degree", "gg_pagerank", "gg_betweenness", "gg_clustering"]

    def contribute(self, ctx: PipelineContext, node: dict, row: dict):
        t = ctx._topo
        nid = node["id"]
        row["gg_degree"] = t["deg"].get(nid, 0)
        row["gg_pagerank"] = t["pr"].get(nid, 0.0)
        row["gg_betweenness"] = t["bc"].get(nid, 0.0)
        row["gg_clustering"] = t["cl"].get(nid, 0.0)

    def doc(self, ctx: PipelineContext) -> list:
        return [
            ("gg_degree", "数值", "游戏投影图中的度数（相似玩法邻居数）"),
            ("gg_pagerank", "数值", "游戏投影图 PageRank（玩法网中心性）"),
            ("gg_betweenness", "数值", "游戏投影图介数（桥接不同玩法社区的程度）"),
            ("gg_clustering", "数值", "游戏投影图聚类系数（邻居间的紧密度）"),
        ]


# --------------------------------------------------------------------------
# 属性因子：评分 / 年份 / 类型数 / 设计维度
# --------------------------------------------------------------------------
@FeatureGroup.register
class AttributeFeatures(FeatureGroup):
    name = "attributes"

    def columns(self, ctx: PipelineContext) -> list:
        return ["year", "player_rating", "n_genres",
                "has_open_world", "has_coop", "has_online"]

    def contribute(self, ctx: PipelineContext, node: dict, row: dict):
        r = node["raw"]
        genres = r.get("genres", [])
        dd = ctx.config.design_dims
        rating = r.get("player_rating")
        row["year"] = r.get("year")
        row["player_rating"] = rating if rating not in (None, "") else np.nan
        row["n_genres"] = len([x for x in genres if x not in dd])
        row["has_open_world"] = 1 if "开放世界" in genres else 0
        row["has_coop"] = 1 if "多人合作" in genres else 0
        row["has_online"] = 1 if "在线" in genres else 0

    def doc(self, ctx: PipelineContext) -> list:
        return [
            ("year", "数值", "发行/获奖年份"),
            ("player_rating", "数值", "Metacritic 媒体均分（0-100，缺失已填补）"),
            ("n_genres", "数值", "所属玩法原子类型数量(不含设计维度)"),
            ("has_open_world", "0/1", "是否开放世界（设计维度）"),
            ("has_coop", "0/1", "是否多人合作（设计维度）"),
            ("has_online", "0/1", "是否在线（设计维度）"),
        ]


# --------------------------------------------------------------------------
# 声誉因子：工作室夺冠数 / 作品数 / 全局 PageRank
# --------------------------------------------------------------------------
@FeatureGroup.register
class ReputationFeatures(FeatureGroup):
    name = "reputation"

    def prepare(self, ctx: PipelineContext):
        games = ctx.games
        wins = defaultdict(int)
        n_games = defaultdict(int)
        for n in games:
            did = n["raw"].get("developer_id")
            n_games[did] += 1
            if n["raw"].get("is_goty"):
                wins[did] += 1
        ctx._rep = {"wins": wins, "n_games": n_games}
        # 工作室在全异构图的 PageRank（声誉强度）
        G = build_full_nx(ctx.graph)
        pr = nx.pagerank(G, alpha=0.85)
        ctx._studio_pr = {n["id"]: pr.get(n["id"], 0.0)
                          for n in ctx.graph["nodes"] if n["group"] == "studio"}

    def columns(self, ctx: PipelineContext) -> list:
        cols = ["studio_n_games", "studio_pagerank"]
        if ctx.config.features.include_studio_wins:
            cols.insert(0, "studio_wins")
        return cols

    def contribute(self, ctx: PipelineContext, node: dict, row: dict):
        did = node["raw"].get("developer_id")
        rep = ctx._rep
        if ctx.config.features.include_studio_wins:
            row["studio_wins"] = rep["wins"].get(did, 0)
        row["studio_n_games"] = rep["n_games"].get(did, 0)
        row["studio_pagerank"] = ctx._studio_pr.get(did, 0.0)

    def doc(self, ctx: PipelineContext) -> list:
        doc = [
            ("studio_n_games", "数值", "工作室在数据集中的作品总数（产出）"),
            ("studio_pagerank", "数值", "工作室在全图的 PageRank（声誉强度）"),
        ]
        if ctx.config.features.include_studio_wins:
            doc.insert(0, ("studio_wins", "数值",
                           "工作室年度最佳夺冠次数（由 is_goty 派生，存在标签泄漏，可关闭）"))
        return doc


# --------------------------------------------------------------------------
# 类型 one-hot：每个玩法叶子类型一列
# --------------------------------------------------------------------------
@FeatureGroup.register
class GenreOneHotFeatures(FeatureGroup):
    name = "genre_onehot"

    def columns(self, ctx: PipelineContext) -> list:
        return [f"g_{gn}" for gn in ctx.genre_names]

    def contribute(self, ctx: PipelineContext, node: dict, row: dict):
        genres = set(node["raw"].get("genres", []))
        for gn in ctx.genre_names:
            row[f"g_{gn}"] = 1 if gn in genres else 0

    def doc(self, ctx: PipelineContext) -> list:
        return [(f"g_{gn}", "0/1", f"是否属于玩法类型「{gn}」") for gn in ctx.genre_names]


# --------------------------------------------------------------------------
# 引擎
# --------------------------------------------------------------------------
class FeatureEngine:
    def __init__(self, config):
        self.config = config

    def extract(self, ctx: PipelineContext):
        enabled = [g for g in self.config.features.groups
                   if g in FeatureGroup._registry]
        unknown = [g for g in self.config.features.groups
                   if g not in FeatureGroup._registry]
        if unknown:
            raise ValueError(f"未知特征组：{unknown}；可用：{list(FeatureGroup._registry)}")

        groups = [FeatureGroup._registry[name](self.config) for name in enabled]
        for g in groups:
            g.prepare(ctx)

        id_cols = ["game_id", "title", "title_zh", "developer_id", "developer", "is_goty"]
        rows = []
        for n in ctx.games:
            row = {
                "game_id": n["id"],
                "title": n["raw"].get("title"),
                "title_zh": n["raw"].get("title_zh"),
                "developer_id": n["raw"].get("developer_id"),
                "developer": n["raw"].get("developer"),
                "is_goty": 1 if n["raw"].get("is_goty") else 0,
            }
            for g in groups:
                g.contribute(ctx, n, row)
            rows.append(row)

        df = pd.DataFrame(rows)
        n_imputed = self._impute(df, self.config.features.impute_rating)
        ctx.rating_imputed_n = n_imputed

        doc = []
        for g in groups:
            doc += g.doc(ctx)
        return df, doc

    @staticmethod
    def _impute(df: pd.DataFrame, mode: str) -> int:
        col = "player_rating"
        if col not in df.columns:
            return 0
        n_missing = int(df[col].isna().sum())
        if n_missing == 0:
            return 0
        if mode == "median":
            df[col] = df[col].fillna(df[col].median())
        elif mode == "mean":
            df[col] = df[col].fillna(df[col].mean())
        else:  # zero
            df[col] = df[col].fillna(0)
        return n_missing
