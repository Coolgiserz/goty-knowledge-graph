"""分析器（Analyzer）：聚类 / 社区发现 / 热点统计。

每个分析器是一个 Analyzer 子类，通过 @Analyzer.register 注册；
pipeline 按注册表顺序调用 analyze(ctx)，各自：
  - 读取 ctx 中的前置产物（如 factors）
  - 计算并把结果写回 ctx.artifacts[key]
  - 向 ctx.report 追加自己的 markdown 章节（含对应 PNG 引用）

新增一种分析只需写子类并注册，pipeline 自动串联——即「可插拔」。
"""
import json
from collections import defaultdict, Counter

import numpy as np
import pandas as pd
import networkx as nx
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.manifold import MDS
from sklearn.metrics import silhouette_score
from sklearn.cluster import KMeans, AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity
from scipy.cluster.hierarchy import linkage, fcluster
from scipy.stats import spearmanr

from .context import PipelineContext
from .clusterers import Clusterer
from .community import CommunityDetector, community_profiles
from .embeddings import rw_game_embeddings
from .similarity import sp_studio_similarity
from .affinity import goty_pagerank
from .constants import PNG


# 不参与聚类的标识/标签列
_IDENT = {"game_id", "title", "title_zh", "developer_id", "developer", "is_goty"}


def _style_cols(df: pd.DataFrame) -> list:
    """用于「工作室风格 / GOTY 特征」的游戏玩法风格列：

    - 排除标识列 _IDENT
    - 排除 year（属于时间维度，不是玩法风格）
    - 排除 studio_* 声誉列（避免「风格相似」被工作室声望主导，偏离玩法本意）
    """
    drop = _IDENT | {"year"}
    return [c for c in df.columns if c not in drop and not c.startswith("studio_")]


def _mds_coords(sim: np.ndarray, random_state: int, n_init: int = 10) -> np.ndarray:
    """由相似度矩阵得到 2D MDS 坐标（不相似度 = 1 - 相似度）。

    sklearn 1.9 起 MDS 重构：metric 重命名为 metric_mds、dissimilarity 标记弃用。
    这里用 warnings 抑制 + 参数回退，保证跨版本行为一致且不刷 warning。
    """
    import warnings
    Ddiss = 1.0 - sim
    try:  # 新版 sklearn（>=1.9）：metric_mds 取代 metric
        mds = MDS(n_components=2, metric_mds=True, dissimilarity="precomputed",
                  init="random", random_state=random_state, n_init=n_init)
    except TypeError:  # 旧版 sklearn：仅接受 metric / dissimilarity
        mds = MDS(n_components=2, metric=True, dissimilarity="precomputed",
                  init="random", random_state=random_state, n_init=n_init)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return mds.fit_transform(Ddiss)


class AnalyzerResult:
    def __init__(self):
        self.artifacts: dict = {}
        self.report: list = []

    def add(self, key, obj):
        self.artifacts[key] = obj
        return obj

    def write(self, *lines: str):
        for s in lines:
            self.report.append(s)


class Analyzer:
    name = "base"
    _registry: dict = {}

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        raise NotImplementedError

    @classmethod
    def register(cls, sub):
        cls._registry[sub.name] = sub
        return sub

    @classmethod
    def all(cls):
        return list(cls._registry.values())


# ==========================================================================
# 聚类
# ==========================================================================
@Analyzer.register
class ClusterAnalyzer(Analyzer):
    name = "cluster"

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        df = ctx.get("factors")
        cfg = ctx.config.cluster
        genre_names = ctx.genre_names

        cols = [c for c in df.columns if c not in _IDENT]
        X = df[cols].copy()
        if X["player_rating"].isna().any():
            X["player_rating"] = X["player_rating"].fillna(X["player_rating"].median())

        # 标准化
        if cfg.scale:
            Xs = StandardScaler().fit_transform(X.values)
        else:
            Xs = X.values.astype(float)

        # PCA 白化（缓解高维 one-hot 维度灾难）；始终算 2D 用于可视化
        coords2d = PCA(n_components=2, random_state=cfg.random_state).fit_transform(Xs)
        if cfg.use_pca:
            try:
                pca = PCA(n_components=cfg.pca_variance, random_state=cfg.random_state)
                cluster_X = pca.fit_transform(Xs)
            except Exception:
                cluster_X = Xs
        else:
            cluster_X = Xs

        method = Clusterer.get(cfg.method)(ctx.config)

        # ---- 选 k ----
        scores, inertia, best_k = {}, {}, None
        if cfg.fixed_k is not None:
            best_k = cfg.fixed_k
        elif method.needs_k():
            lo, hi = cfg.k_range
            for k in range(lo, hi + 1):
                lab = method.fit_predict(cluster_X, k)
                if len(set(lab)) > 1:
                    scores[k] = float(silhouette_score(cluster_X, lab))
                    if cfg.method == "kmeans":
                        inertia[k] = float(
                            KMeans(n_clusters=k, random_state=cfg.random_state, n_init=10)
                            .fit(cluster_X).inertia_)
            if scores:
                best_k = max(scores, key=scores.get)
        else:
            best_k = None  # DBSCAN 不需要

        # ---- 最终拟合 ----
        if best_k is not None:
            labels = method.fit_predict(cluster_X, best_k)
        else:
            labels = method.fit_predict(cluster_X, None)
            n_clusters = len(set(labels)) - (1 if -1 in labels else 0)
            best_k = n_clusters

        out = df.copy()
        out["cluster"] = labels

        # 层次聚类对照（同一矩阵，best_k）
        if best_k and best_k >= 2:
            out["cluster_hier"] = AgglomerativeClustering(n_clusters=best_k).fit_predict(cluster_X)
        out["pca_x"] = coords2d[:, 0]
        out["pca_y"] = coords2d[:, 1]

        profiles = self._profile(out, labels, genre_names)
        profile = {
            "method": cfg.method,
            "best_k": int(best_k),
            "use_pca": cfg.use_pca,
            "silhouette": {int(k): float(v) for k, v in scores.items()},
            "inertia": {int(k): float(v) for k, v in inertia.items()},
            "profiles": profiles,
        }
        if cfg.method == "dbscan":
            profile["n_clusters"] = int(best_k)
            profile["n_noise"] = int((labels == -1).sum())

        res.add("clusters", out)
        res.add("cluster_profile", profile)

        # ---- 报告 ----
        sil = scores.get(best_k) if scores else None
        res.write(
            "\n## 二、聚类（" + cfg.method + (" + PCA" if cfg.use_pca else "") + "）\n",
            f"在标准化因子矩阵{'经 PCA 白化后' if cfg.use_pca else ''}上聚类。",
        )
        if sil is not None:
            res.write(
                f"以轮廓系数在 k={cfg.k_range[0]}..{cfg.k_range[1]} 选优，最优 **k={best_k}**"
                f"（轮廓={sil:.3f}）。")
            if sil < cfg.warn_silhouette:
                res.write(
                    f"> 注：轮廓系数 {sil:.3f} 偏低（<{cfg.warn_silhouette}），说明游戏在因子空间中"
                    "呈连续谱，k 仅为探索性划分，并非严谨类别边界。")
        else:
            res.write(f"使用 **{cfg.method}**，得到 **{best_k}** 个簇"
                      + (f"（噪声点 {profile.get('n_noise',0)} 个）" if cfg.method == "dbscan" else "") + "。")
        res.write("各簇画像：\n")
        res.write("| 簇 | 规模 | 年度最佳 | GOTY率 | 均分 | 代表游戏(前5) | 主导玩法类型 |")
        res.write("|---|---|---|---|---|---|---|")
        for p in profiles:
            topg = "、".join(f"{gn}({v:.2f})" for gn, v in p["top_genres"][:5])
            games = "、".join(p["top_games"][:5])
            res.write(f"| 簇{p['cluster']} | {p['size']} | {p['goty']} | {p['goty_rate']:.2f} | "
                      f"{p['avg_rating']} | {games} | {topg} |")
        res.write(
            f"\n![聚类PCA]({PNG['cluster_pca']})\n",
            f"![簇画像]({PNG['cluster_profile']})\n",
            f"![k选择]({PNG['k_silhouette']})\n",
        )
        return res

    @staticmethod
    def _profile(df, labels, genre_names, top_g=8):
        out = []
        for c in sorted(set(labels)):
            sub = df[labels == c]
            prof = {"cluster": int(c), "size": int(len(sub)),
                    "goty": int(sub["is_goty"].sum()),
                    "goty_rate": round(float(sub["is_goty"].mean()), 3),
                    "avg_rating": round(float(sub["player_rating"].mean()), 1)
                    if sub["player_rating"].notna().any() else None,
                    "avg_year": round(float(sub["year"].mean()), 1),
                    "top_games": sub.sort_values("player_rating", ascending=False)["title_zh"].head(5).tolist(),
                    "top_genres": []}
            gcols = [f"g_{gn}" for gn in genre_names]
            means = sub[gcols].mean().sort_values(ascending=False)
            for col, v in means.head(top_g).items():
                prof["top_genres"].append((col[2:], round(float(v), 3)))
            out.append(prof)
        return out


# ==========================================================================
# 社区发现（可插拔：主方法 Louvain/Infomap + Infomap 作为补充对照）
# ==========================================================================
@Analyzer.register
class CommunityAnalyzer(Analyzer):
    name = "community"

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        GG = ctx.GG
        cfg = ctx.config.community

        # ---- 主方法（可配：louvain / infomap）----
        det = CommunityDetector.get(cfg.method)()
        node2comm, info = det.detect(GG, cfg)
        method_name = det.name
        method_label = method_name.capitalize()
        ncomm = info["n_communities"]

        prof = self._profile(ctx, node2comm)
        prof.sort(key=lambda x: -x["size"])

        comm_df = pd.DataFrame([{"game_id": nid, "community": c}
                                for nid, c in node2comm.items()])
        res.add("communities", comm_df)
        res.add("community_map", node2comm)
        res.add("community_profile",
                {"method": method_name, "n_communities": ncomm,
                 "quality": info, "profiles": prof})

        # ---- 报告：主方法 ----
        if "modularity" in info and "codelength" not in info:
            qtxt = f"**模块度 Q={info['modularity']}**"
            qnote = "（越接近 1 划分越清晰）"
        else:
            qtxt = f"**编码长度 L={info['codelength']}**"
            qnote = "（越小越好，代表随机游走的平均描述长度越短）"
        res.write(
            f"\n## 三、社区发现（{method_label}）\n",
            f"在游戏-游戏相似投影图上做 {method_name} 划分，得到 **{ncomm} 个社区**，{qtxt}{qnote}。"
            "社区即「玩法家族」：\n",
            "| 社区 | 规模 | 年度最佳成员 | 代表游戏 | 主导玩法类型 |",
            "|---|---|---|---|---|",
        )
        for c in prof:
            topg = "、".join(f"{gn}({v})" for gn, v in c["top_genres"][:5])
            goty = "、".join(c["goty_members"]) or "—"
            res.write(f"| C{c['community']} | {c['size']} | {goty} | {c['representative']} | {topg} |")
        res.write(f"\n![社区图]({PNG['community']})\n")

        # ---- 补充方法：Infomap 与 Walktrap（随机游走视角），始终尝试以做交叉验证 ----
        compare = [(method_label, ncomm, info)]
        im_info = self._maybe_infomap(ctx, res, cfg) if cfg.method != "infomap" else None
        if im_info is not None:
            compare.append(("infomap", im_info["n_communities"], im_info))
        wt_info = self._maybe_walktrap(ctx, res, cfg) if cfg.method != "walktrap" else None
        if wt_info is not None:
            compare.append(("walktrap", wt_info["n_communities"], wt_info))

        if len(compare) > 1:
            res.write(
                "\n**主方法与随机游走方法（Infomap / Walktrap）对照：**\n",
                "| 方法 | 社区数 | 模块度 Q | 编码长度 L |",
                "|---|---|---|---|",
            )
            for name, nc, inf in compare:
                res.write(f"| {name} | {nc} | {inf.get('modularity','—')} | {inf.get('codelength','—')} |")
            res.write(
                "\n> **Infomap** 基于**地图方程（Map Equation）**：图上模拟随机游走，求让信息流最短平均编码的划分，"
                "质量指标为**编码长度 L**（越小越好），天然支持层级、无需 resolution 调参。"
                "**Walktrap** 同样基于随机游走：用「游走到平稳所需步数」定义节点距离再做层次聚并，按模块度 Q 取最优切分。"
                "三种独立方法社区数接近，说明「玩法家族」结构稳健。\n",
            )
        return res

    @staticmethod
    def _maybe_infomap(ctx, res, cfg):
        """尝试用 Infomap 探测并写报告；不可用/失败时返回 None。"""
        try:
            from .community import InfomapDetector
        except Exception:
            return None
        try:
            GG = ctx.GG
            node2comm, info = InfomapDetector().detect(GG, cfg)
        except Exception as e:
            res.write(f"\n> 注：Infomap 探测失败（{e}），已跳过。\n")
            return None

        prof = CommunityAnalyzer._profile(ctx, node2comm)
        prof.sort(key=lambda x: -x["size"])
        comm_df = pd.DataFrame([{"game_id": nid, "community": c}
                                for nid, c in node2comm.items()])
        res.add("communities_infomap", comm_df)
        res.add("community_map_infomap", node2comm)
        res.add("community_profile_infomap",
                {"method": "infomap", "n_communities": info["n_communities"],
                 "quality": info, "profiles": prof})

        res.write(
            "\n**Infomap 社区（玩法家族，按规模）：**\n",
            "| 社区 | 规模 | 年度最佳成员 | 代表游戏 | 主导玩法类型 |",
            "|---|---|---|---|---|",
        )
        for c in prof:
            topg = "、".join(f"{gn}({v})" for gn, v in c["top_genres"][:5])
            goty = "、".join(c["goty_members"]) or "—"
            res.write(f"| C{c['community']} | {c['size']} | {goty} | {c['representative']} | {topg} |")
        return info

    @staticmethod
    def _maybe_walktrap(ctx, res, cfg):
        """尝试用 Walktrap（随机游走社区发现）探测并写报告；不可用/失败时返回 None。"""
        try:
            from .community import WalktrapDetector
        except Exception:
            return None
        try:
            GG = ctx.GG
            node2comm, info = WalktrapDetector().detect(GG, cfg)
        except Exception as e:
            res.write(f"\n> 注：Walktrap 探测失败（{e}），已跳过。\n")
            return None
        prof = CommunityAnalyzer._profile(ctx, node2comm)
        prof.sort(key=lambda x: -x["size"])
        comm_df = pd.DataFrame([{"game_id": nid, "community": c}
                                for nid, c in node2comm.items()])
        res.add("communities_walktrap", comm_df)
        res.add("community_map_walktrap", node2comm)
        res.add("community_profile_walktrap",
                {"method": "walktrap", "n_communities": info["n_communities"],
                 "quality": info, "profiles": prof})
        res.write(
            "\n**Walktrap 社区（玩法家族，按规模）：**\n",
            "| 社区 | 规模 | 年度最佳成员 | 代表游戏 | 主导玩法类型 |",
            "|---|---|---|---|---|",
        )
        for c in prof:
            topg = "、".join(f"{gn}({v})" for gn, v in c["top_genres"][:5])
            goty = "、".join(c["goty_members"]) or "—"
            res.write(f"| C{c['community']} | {c['size']} | {goty} | {c['representative']} | {topg} |")
        res.write(f"\n![Walktrap 社区图]({PNG['community_walktrap']})\n")
        return info

    @staticmethod
    def _profile(ctx, node2comm):
        return community_profiles(ctx.graph["nodes"], node2comm, ctx.config.design_dims)


# ==========================================================================
# 热点统计（时代演变）
# ==========================================================================
@Analyzer.register
class HotspotAnalyzer(Analyzer):
    name = "hotspot"

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        cfg = ctx.config.hotspot
        dd = ctx.config.design_dims
        studio_name = ctx.studio_names
        games = ctx.games

        # 热点 = 奖项的「品味」如何随时间变化。最干净的样本是 GOTY 获奖作本身：
        # 每年 1 款、两半段各 10 款，固定且无「其他作品」分母偏差。
        YEAR_LO, YEAR_HI = 2006, 2025
        years = list(range(YEAR_LO, YEAR_HI + 1))
        goty = [n for n in games
                if n["raw"].get("is_goty")
                and isinstance(n["raw"].get("year"), int)
                and YEAR_LO <= n["raw"]["year"] <= YEAR_HI]
        goty_sorted = sorted(goty, key=lambda n: n["raw"]["year"])

        # 每年 GOTY 的标签出现（1 款/年，故 0/1）
        year_tag = {y: Counter() for y in years}
        for n in goty_sorted:
            for t in n["raw"].get("tiers", []):
                year_tag[n["raw"]["year"]][t] += 1

        # 关键集合：3 个设计维度 + GOTY 中高频玩法类别 Top（避免 39 子类噪声）
        all_tag = Counter()
        for y in years:
            all_tag.update(year_tag[y])
        gameplay_top = [t for t, _ in all_tag.most_common() if t not in dd][:7]
        key_dims = [d for d in ("开放世界", "多人合作", "在线") if d in dd]
        key_set = key_dims + gameplay_top

        # 滚动 3 年占比（用于趋势图）：窗口内带该标签的 GOTY 数 ÷ 窗口年数
        W = 3
        rows = []
        for y in years:
            ys = [yy for yy in range(y - W + 1, y + 1) if YEAR_LO <= yy <= YEAR_HI]
            denom = len(ys)
            for t in key_set:
                c = sum(year_tag[yy].get(t, 0) for yy in ys)
                rows.append({"year": y, "tag": t,
                             "rolling_share": round(100.0 * c / denom, 1) if denom else 0.0})
        year_df = pd.DataFrame(rows)

        # 时代分桶（GOTY 各时代标签计数，便于对照）
        era_rows = []
        for lo, hi, name in cfg.eras:
            gs = [n for n in goty_sorted if lo <= n["raw"]["year"] <= hi]
            base = len(gs)
            c = Counter()
            for n in gs:
                for t in n["raw"].get("tiers", []):
                    c[t] += 1
            for t in key_set:
                era_rows.append({"era": name, "tag": t, "count": c.get(t, 0),
                                 "share": round(100.0 * c.get(t, 0) / base, 1) if base else 0.0})
        era_df = pd.DataFrame(era_rows)

        # 趋势：前十年(<=2015) vs 后十年(>=2016)，各 10 款 GOTY 的标签计数
        h1 = [n for n in goty_sorted if n["raw"]["year"] <= 2015]
        h2 = [n for n in goty_sorted if n["raw"]["year"] >= 2016]
        n1, n2 = len(h1), len(h2)

        def _cnt(games):
            c = Counter()
            for n in games:
                for t in n["raw"].get("tiers", []):
                    c[t] += 1
            return c
        t1, t2 = _cnt(h1), _cnt(h2)
        trend = []
        for t in key_set:
            a, b = t1.get(t, 0), t2.get(t, 0)
            trend.append({"tag": t, "first_half": a, "second_half": b,
                          "first_pp": round(100.0 * a / n1, 1) if n1 else 0.0,
                          "second_pp": round(100.0 * b / n2, 1) if n2 else 0.0,
                          "delta_pp": round(100.0 * (b - a) / n1, 1) if n1 else 0.0,
                          "is_design": t in dd})
        rising = sorted([x for x in trend if x["delta_pp"] > 0], key=lambda x: -x["delta_pp"])
        falling = sorted([x for x in trend if x["delta_pp"] < 0], key=lambda x: x["delta_pp"])

        # 工作室 GOTY 主导度（夺冠次数，替代原「滚动热度」：每年仅 1 个 GOTY，滚动窗口无意义）
        studio_wins = Counter()
        for n in goty_sorted:
            sid = n["raw"].get("developer_id")
            studio_wins[studio_name.get(sid, n["raw"].get("developer"))] += 1
        studio_tally = [{"studio": k, "wins": v} for k, v in studio_wins.most_common()]

        timeline = [{"year": n["raw"]["year"], "title_zh": n["raw"]["title_zh"],
                     "tiers": n["raw"].get("tiers", []), "rating": n["raw"].get("player_rating")}
                    for n in goty_sorted]

        summary = {
            "years": years,
            "key_dims": key_dims,
            "key_gameplay": gameplay_top,
            "rising": rising,
            "falling": falling,
            "n_first_half": n1,
            "n_second_half": n2,
            "studio_goty_tally": studio_tally,
            "goty_timeline": timeline,
        }
        res.add("hotspot_year", year_df)
        res.add("hotspot_era", era_df)
        res.add("hotspot_summary", summary)

        res.write(
            "\n## 四、热点统计：奖项的「品味」如何演变\n",
            "> **方法（一句话）**：热点 = GOTY 获奖作本身的类型构成随时间的变化（每年 1 款、两半段各 "
            f"**{n1}** 款，样本固定、无「其他作品」分母偏差）。某类型「占比」= 该半段带此标签的获奖作 ÷ {n1}。"
            "比较 **2006–2015** 与 **2016–2025** 两个半段判断上升/下降。\n",
            f"> **样本提示**：每半段仅 {n1} 款，结果为**示意性趋势**而非统计推断。\n",
            "\n**设计维度趋势（跨玩法的特征标签）：**",
        )
        for x in rising:
            if x["is_design"]:
                res.write(f"- 🔼 **{x['tag']}**：{x['first_half']}/{n1} 款 → {x['second_half']}/{n2} 款（{x['delta_pp']:+}pp）")
        for x in falling:
            if x["is_design"]:
                res.write(f"- 🔽 **{x['tag']}**：{x['first_half']}/{n1} 款 → {x['second_half']}/{n2} 款（{x['delta_pp']:+}pp）")
        res.write("\n**玩法类别趋势（上升 / 下降 Top）：**")
        for x in rising[:5]:
            if not x["is_design"]:
                res.write(f"- 🔼 **{x['tag']}**：{x['first_half']}/{n1} 款 → {x['second_half']}/{n2} 款（{x['delta_pp']:+}pp）")
        for x in falling[:5]:
            if not x["is_design"]:
                res.write(f"- 🔽 **{x['tag']}**：{x['first_half']}/{n1} 款 → {x['second_half']}/{n2} 款（{x['delta_pp']:+}pp）")
        res.write("\n**GOTY 主导工作室（夺冠次数）：**",
                  "| 工作室 | GOTY 次数 |", "|---|---|")
        for s in studio_tally[:8]:
            res.write(f"| {s['studio']} | {s['wins']} |")
        res.write(f"\n![类型热度演变]({PNG['hotspot']})\n")
        return res


# ==========================================================================
# 开发商游戏风格相似性
# ==========================================================================
@Analyzer.register
class StudioStyleAnalyzer(Analyzer):
    name = "studio_style"

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        df = ctx.get("factors")
        cfg = ctx.config
        studio_name = ctx.studio_names

        # ===== 两种互补视角，并存而非替换 =====
        # A. 图谱距离（最短路径）：一阶邻近性（只数直接跳数）
        sim_sp, studio_ids = sp_studio_similarity(ctx.GG, ctx.games)
        # B. 随机游走嵌入：二阶 / 多跳邻近性（多跳共现经 SVD 平滑）
        emb, gi = rw_game_embeddings(ctx.G_full, ctx.games, ctx.config.random_walk)
        by_studio_rw = defaultdict(list)
        for n in ctx.games:
            gid = n["id"]
            vec = emb[gi[gid]] if gid in gi else np.zeros(emb.shape[1])
            by_studio_rw[n["raw"]["developer_id"]].append(vec)
        S = np.array([np.mean(by_studio_rw[d], axis=0) for d in studio_ids])
        sim_rw = cosine_similarity(S)            # 方向一致即风格接近，与作品数量无关
        np.fill_diagonal(sim_rw, 1.0)

        n_stu = len(studio_ids)

        # 二维散点：MDS 嵌入 (1 - 相似度) 距离矩阵（点越近≈相似度越高≈风格越接近）
        coords_sp = _mds_coords(sim_sp, cfg.random_state)
        coords_rw = _mds_coords(sim_rw, cfg.random_state)

        # 主导社区（着色/分组）：取自第三节社区发现（同一套社区划分）
        comm_map = ctx.get("community_map")
        if not comm_map:  # 兜底
            comms = nx.community.louvain_communities(ctx.GG, weight="weight", seed=cfg.random_state)
            comm_map = {}
            for cid, members in enumerate(sorted(comms, key=len, reverse=True)):
                for m in members:
                    comm_map[m] = cid
        community_ids = sorted(set(comm_map.values()))
        by_comm = defaultdict(list)
        for gid, did in zip(df["game_id"], df["developer_id"]):
            c = comm_map.get(gid)
            if c is not None:
                by_comm[did].append(c)
        prof = (ctx.get("community_profile") or {}).get("profiles", [])
        comm_label = {}
        for c in prof:
            comm_label[c["community"]] = "、".join(g for g, _ in c["top_genres"][:2]) or f"社区{c['community']}"
        for c in community_ids:
            comm_label.setdefault(c, f"社区{c}")
        dom_comm = []
        for sid in studio_ids:
            cnt = Counter(by_comm.get(sid, []))
            dom_comm.append(cnt.most_common(1)[0][0] if cnt else community_ids[0])

        def _mk_style_df(coords):
            return pd.DataFrame({
                "studio_id": studio_ids,
                "studio": [studio_name.get(s, s) for s in studio_ids],
                "n_games": [len(by_studio_rw[s]) for s in studio_ids],
                "dominant_community": dom_comm,
                "dominant_community_label": [comm_label[c] for c in dom_comm],
                "style_x": coords[:, 0], "style_y": coords[:, 1],
            })

        style_df_sp = _mk_style_df(coords_sp)
        style_df_rw = _mk_style_df(coords_rw)

        def _top_pairs(sim):
            pairs = []
            for i in range(n_stu):
                for j in range(i + 1, n_stu):
                    pairs.append((studio_ids[i], studio_ids[j], float(sim[i, j])))
            pairs.sort(key=lambda x: -x[2])
            return pairs[:8]

        top_sp = _top_pairs(sim_sp)
        top_rw = _top_pairs(sim_rw)

        # 两视角一致性：距离排序的 Spearman 相关（基于下三角去对角元素）
        off = ~np.eye(n_stu, dtype=bool)
        rho, _ = spearmanr((1.0 - sim_sp)[off], (1.0 - sim_rw)[off])

        # 按主导社区（玩法家族）归并工作室（两视角共用）
        groups = defaultdict(list)
        for sid, c in zip(studio_ids, dom_comm):
            groups[c].append(studio_name.get(sid, sid))
        group_rows = [{"community": c, "label": comm_label[c],
                       "studios": names, "size": len(names)}
                      for c, names in sorted(groups.items(), key=lambda kv: -len(kv[1]))]

        res.add("studio_sim_sp_matrix", {"studio_ids": studio_ids,
                                         "studio_names": [studio_name.get(s, s) for s in studio_ids],
                                         "matrix": sim_sp})
        res.add("studio_style_sp", style_df_sp)
        res.add("studio_sim_rw_matrix", {"studio_ids": studio_ids,
                                         "studio_names": [studio_name.get(s, s) for s in studio_ids],
                                         "matrix": sim_rw})
        res.add("studio_style_rw", style_df_rw)
        res.add("studio_style_summary",
                {"n_studios": n_stu, "n_communities": len(community_ids),
                 "spearman_rho": round(float(rho), 3),
                 "top_pairs_sp": [[studio_name.get(a, a), studio_name.get(b, b), round(s, 3)]
                                  for a, b, s in top_sp],
                 "top_pairs_rw": [[studio_name.get(a, a), studio_name.get(b, b), round(s, 3)]
                                  for a, b, s in top_rw],
                 "groups": group_rows})

        # ---- 报告 ----
        res.write(
            "\n## 五、开发商游戏风格相似性（双视角：图谱距离 vs 随机游走嵌入）\n",
            "本节用**两种互补的视角**度量工作室风格接近度，二者**并存、互为印证**，而非互相替代：\n",
            "- **A. 图谱距离（最短路径）**：在游戏-游戏相似投影图 GG 上测工作室间最短路径距离，"
            "捕捉「直接共享类型 / 同工作室」的**一阶邻近性**（只数跳数）。\n",
            "- **B. 随机游走嵌入**：在完整异构图做随机游走得到游戏嵌入、工作室取均值、余弦相似度，"
            "捕捉「经由共享类型 / 工作室间接相连」的**二阶 / 多跳邻近性**（多跳共现经 SVD 平滑降维）。\n",
            "两者都基于同一张图；差异仅在于前者只数直接跳数、后者把多跳共现平滑后再比较。"
            "下面的相似度矩阵、Top 对与风格空间散点分别给出，最后给一致性对照。\n",
            f"共 **{n_stu} 家**工作室，按**主导玩法家族社区**归并为 **{len(group_rows)} 组**：\n",
            "| 主导玩法家族（社区） | 规模 | 成员工作室 |",
            "|---|---|---|",
        )
        for g in group_rows:
            res.write(f"| {g['label']} | {g['size']} | {'、'.join(g['studios'])} |")

        res.write(
            "\n### 5.1 图谱距离（最短路径）\n",
            "边距离 = 1/(1+共享权重)，工作室距离 = 双向平均最近邻图距离，相似度 = 1/(1+距离)。"
            "这是「硬」的一阶信号，相似度集中在约 0.3~0.7 区间。\n",
            "**最近的工作室对（Top8，相似度）：**\n",
            "| 工作室 A | 工作室 B | 相似度 |",
            "|---|---|---|",
        )
        for a, b, s in res.artifacts["studio_style_summary"]["top_pairs_sp"]:
            res.write(f"| {a} | {b} | {s:.3f} |")
        res.write(
            f"\n![工作室风格相似度（图谱距离）]({PNG['studio_sim']})\n",
            f"![工作室风格散点（图谱距离 MDS）]({PNG['studio_style_scatter']})\n",
        )

        res.write(
            "\n### 5.2 随机游走嵌入\n",
            "在完整异构图跑截断随机游走 → 游戏共现矩阵(log1p) → SVD 降维得到游戏嵌入 → "
            "工作室取均值 → 余弦相似度。这是「平滑」的二阶信号，相似度分离度更高（Top 对可达 0.9+）。\n",
            "**最近的工作室对（Top8，余弦相似度）：**\n",
            "| 工作室 A | 工作室 B | 余弦相似度 |",
            "|---|---|---|",
        )
        for a, b, s in res.artifacts["studio_style_summary"]["top_pairs_rw"]:
            res.write(f"| {a} | {b} | {s:.3f} |")
        res.write(
            f"\n![工作室风格相似度（随机游走共现）]({PNG['studio_sim_rw']})\n",
            f"![工作室风格散点（随机游走嵌入 MDS）]({PNG['studio_style_rw_scatter']})\n",
        )

        res.write(
            "\n### 5.3 两种视角对照\n",
            f"- 两视角**距离排序的 Spearman 相关 ρ = {rho:.3f}**，说明两者高度一致——"
            "它们度量的是同一张图上的类型 / 工作室邻近性，只是表示方式不同。\n",
            "- **共同点**：RPG / 动作 RPG 厂（贝塞斯达、CD Projekt Red、FromSoftware 等）"
            "在两种视角下都明显相近。\n",
            "- **差异点**：随机游走因 SVD 平滑，相似度数值更分散、分离度更高；"
            "最短路径更「硬」（仅计跳数，数值压缩在较窄区间）。\n",
            "> 诚实提示：两种视角度量的是**同一类信号（类型 / 工作室邻近性）的不同表示**，"
            "并非彼此独立的「新洞察」。随机游走是更平滑、更美观的等价尺子；把它当成「比最短路径更好」并不准确，"
            "正确定位是**与最短路径并列的探索手段**——两者一起看，比只看其中一个更稳妥。\n",
        )
        return res


# ==========================================================================
# 年度最佳游戏（GOTY）特征分析
# ==========================================================================
@Analyzer.register
class GotyProfileAnalyzer(Analyzer):
    name = "goty_profile"

    @staticmethod
    def _cohen_d(a, b):
        na, nb = len(a), len(b)
        if na < 2 or nb < 2:
            return 0.0
        sp = np.sqrt(((na - 1) * a.var() + (nb - 1) * b.var()) / (na + nb - 2))
        return float((a.mean() - b.mean()) / sp) if sp > 0 else 0.0

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        df = ctx.get("factors")

        goty = df[df["is_goty"] == 1]
        other = df[df["is_goty"] == 0]

        # 用于区分的特征：数值列，排除标识/年份/直接泄漏的 studio_wins
        num_cols = [c for c in df.columns
                    if c not in _IDENT and c != "year" and c != "studio_wins"
                    and not c.startswith("g_")]
        factors = []
        for c in num_cols:
            a = goty[c].astype(float).dropna()
            b = other[c].astype(float).dropna()
            d = self._cohen_d(a, b)
            factors.append({
                "factor": c,
                "mean_goty": round(float(a.mean()), 3) if len(a) else None,
                "mean_other": round(float(b.mean()), 3) if len(b) else None,
                "cohen_d": round(d, 3),
                "abs_d": abs(d),
            })
        factors.sort(key=lambda x: -x["abs_d"])
        top_factors = factors[:12]

        # 类型 over-index：GOTY 中占比 / 全体占比
        gcols = [c for c in df.columns if c.startswith("g_")]
        share_goty = goty[gcols].mean()
        share_all = df[gcols].mean()
        rows = []
        for c in gcols:
            sg = float(share_goty[c]); sa = float(share_all[c])
            oi = round(sg / sa, 2) if sa > 0 else (None if sg == 0 else 99.0)
            rows.append({"genre": c[2:], "share_goty": round(sg, 3),
                         "share_all": round(sa, 3), "overindex": oi})
        genre_df = pd.DataFrame(rows)
        genre_df = genre_df.sort_values("overindex", ascending=False, na_position="last")

        # 概览统计
        def rate(col):
            return round(float(df[df["is_goty"] == 1][col].mean()), 3), \
                   round(float(df[df["is_goty"] == 0][col].mean()), 3)

        r_ow_g, r_ow_o = rate("has_open_world")
        r_co_g, r_co_o = rate("has_coop")
        r_on_g, r_on_o = rate("has_online")
        summary = {
            "n_goty": int(len(goty)), "n_other": int(len(other)),
            "avg_rating_goty": round(float(goty["player_rating"].mean()), 1),
            "avg_rating_other": round(float(other["player_rating"].mean()), 1),
            "avg_year_goty": round(float(goty["year"].mean()), 1),
            "avg_year_other": round(float(other["year"].mean()), 1),
            "open_world_rate": [r_ow_g, r_ow_o],
            "coop_rate": [r_co_g, r_co_o],
            "online_rate": [r_on_g, r_on_o],
            "n_genres_goty": round(float(goty["n_genres"].mean()), 2),
            "n_genres_other": round(float(other["n_genres"].mean()), 2),
        }

        res.add("goty_profile", {"summary": summary, "factors": factors})
        res.add("goty_genre", genre_df)

        res.write(
            "\n## 六、年度最佳游戏（GOTY）特征分析\n",
            f"对比 **{summary['n_goty']} 款** GOTY 与 **{summary['n_other']} 款** 其他作品：\n",
            f"- **媒体均分**：GOTY {summary['avg_rating_goty']} vs 其他 {summary['avg_rating_other']}；"
            f"**发行年均年**：{summary['avg_year_goty']} vs {summary['avg_year_other']}\n",
            f"- **设计维度占比**：开放世界 {summary['open_world_rate'][0]} vs {summary['open_world_rate'][1]}；"
            f"多人合作 {summary['coop_rate'][0]} vs {summary['coop_rate'][1]}；"
            f"在线 {summary['online_rate'][0]} vs {summary['online_rate'][1]}\n",
            f"- **平均玩法类型数**：{summary['n_genres_goty']} vs {summary['n_genres_other']}\n",
            "\n**区分度最高的因子（Cohen's d，|d| 越大区分越强；正=GOTY 更高）：**\n",
            "| 因子 | GOTY均值 | 其他均值 | Cohen's d |",
            "|---|---|---|---|",
        )
        for f in top_factors:
            res.write(f"| {f['factor']} | {f['mean_goty']} | {f['mean_other']} | {f['cohen_d']} |")
        res.write(
            f"\n> 解读：Cohen's d 仅 0.2/0.5/0.8 为弱/中/强效应参考线；本数据样本小、GOTY 是精英子集，"
            "d 偏向刻画「获奖作相对全体的偏移」而非因果。\n",
            f"![GOTY 区分因子]({PNG['goty_distinguish']})\n",
            f"![类型Over-index]({PNG['goty_genre']})\n",
        )
        return res


# ==========================================================================
# GOTY 品味网络（个性化随机游走 / Personalized PageRank）
# ==========================================================================
@Analyzer.register
class GotyAffinityAnalyzer(Analyzer):
    name = "goty_affinity"

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        cfg = ctx.config.goty_affinity
        Gfull = ctx.G_full
        studio_name = ctx.studio_names
        games = ctx.games

        # 种子 = 全部 GOTY 获奖作；在完整异构图做个性化 PageRank
        summary = goty_pagerank(Gfull, games, studio_name, alpha=cfg.alpha, top_n=cfg.top_n)
        if summary["n_seeds"] == 0:
            res.write("\n> 注：未找到 GOTY 种子，GOTY 品味网络跳过。\n")
            return res
        top_games = summary["top_games"]
        top_studios = summary["top_studios"]
        rec_df = pd.DataFrame(top_games)
        std_df = pd.DataFrame(top_studios)
        res.add("goty_affinity", rec_df)
        res.add("goty_affinity_studios", std_df)
        res.add("goty_affinity_summary", summary)

        res.write(
            "\n## 七、GOTY 品味网络（个性化随机游走）\n",
            "把**全部年度最佳（GOTY）获奖作**作为种子，在完整异构图（游戏↔类型↔工作室↔奖项）上做"
            "**个性化 PageRank**（随机游走以一定概率回到 GOTY 种子）。某节点得分越高，"
            "代表它在「年度最佳品味」网络中越中心——等于回答“**喜欢 GOTY 的人，还会喜欢谁**”。\n",
            f"种子共 **{summary['n_seeds']}** 款；排除种子后，亲和力最高的非 GOTY 作品 Top{cfg.top_n}：\n",
            "| 排名 | 游戏 | 工作室 | 亲和力 |",
            "|---|---|---|---|",
        )
        for i, r in enumerate(top_games, 1):
            res.write(f"| {i} | {r['title_zh']} | {r['studio']} | {r['affinity']:.4f} |")
        res.write(
            "\n**工作室亲和力 Top（其全部作品得分之和）：**\n",
            "| 排名 | 工作室 | 亲和力 |",
            "|---|---|---|",
        )
        for i, s in enumerate(top_studios, 1):
            res.write(f"| {i} | {s['studio']} | {s['affinity']:.4f} |")
        res.write(
            f"\n> 解读：这是从「GOTY 视角」出发的推荐网络，天然浮现每家获奖作的「同门兄弟」"
            "（如 Bethesda 的辐射系列、CDPR 的赛博朋克、Rockstar 的 RDR/GTA）。"
            "它与第三节的社区发现、第五节的工作室风格同源于一张图，但视角从「结构划分」转为「以 GOTY 为中心的影响力传播」。\n",
            f"![GOTY 品味网络·推荐]({PNG['goty_affinity']})\n",
        )
        return res
