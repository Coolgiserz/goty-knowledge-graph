"""分析器（Analyzer）：聚类 / 社区发现 / 热点统计。

每个分析器是一个 Analyzer 子类，通过 @Analyzer.register 注册；
pipeline 按注册表顺序调用 analyze(ctx)，各自：
  - 读取 ctx 中的前置产物（如 factors）
  - 计算并把结果写回 ctx.artifacts[key]
  - 向 ctx.report 追加自己的 markdown 章节（含对应 PNG 引用）

新增一种分析只需写子类并注册，pipeline 自动串联——即「可插拔」。
"""
import json
from collections import defaultdict

import numpy as np
import pandas as pd
import networkx as nx
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.cluster import KMeans, AgglomerativeClustering

from .context import PipelineContext
from .clusterers import Clusterer
from .constants import PNG


# 不参与聚类的标识/标签列
_IDENT = {"game_id", "title", "title_zh", "developer_id", "developer", "is_goty"}


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
# 社区发现（Louvain）
# ==========================================================================
@Analyzer.register
class CommunityAnalyzer(Analyzer):
    name = "community"

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        GG = ctx.GG
        cfg = ctx.config.community
        communities = nx.community.louvain_communities(
            GG, weight="weight", seed=cfg.seed, resolution=cfg.resolution)
        mod = nx.community.modularity(GG, communities, weight="weight")

        comm_sorted = sorted(communities, key=len, reverse=True)
        node2comm = {}
        for cid, members in enumerate(comm_sorted):
            for m in members:
                node2comm[m] = cid
        ncomm = len(comm_sorted)

        prof = self._profile(ctx, node2comm)
        prof.sort(key=lambda x: -x["size"])

        comm_df = pd.DataFrame([{"game_id": nid, "community": c}
                                for nid, c in node2comm.items()])
        res.add("communities", comm_df)
        res.add("community_map", node2comm)
        res.add("community_profile",
                {"n_communities": ncomm, "modularity": round(mod, 4), "profiles": prof})

        res.write(
            "\n## 三、社区发现（Louvain）\n",
            f"在游戏-游戏相似投影图上做 Louvain 划分，得到 **{ncomm} 个社区**，",
            f"**模块度 Q={mod:.4f}**（越接近 1 划分越清晰）。社区即「玩法家族」：\n",
            "| 社区 | 规模 | 年度最佳成员 | 代表游戏 | 主导玩法类型 |",
            "|---|---|---|---|---|",
        )
        for c in prof:
            topg = "、".join(f"{gn}({v})" for gn, v in c["top_genres"][:5])
            goty = "、".join(c["goty_members"]) or "—"
            res.write(f"| C{c['community']} | {c['size']} | {goty} | {c['representative']} | {topg} |")
        res.write(f"\n![社区图]({PNG['community']})\n")
        return res

    @staticmethod
    def _profile(ctx, node2comm):
        byid = {n["id"]: n for n in ctx.graph["nodes"]}
        dd = ctx.config.design_dims
        groups = defaultdict(list)
        for nid, c in node2comm.items():
            groups[c].append(nid)
        out = []
        for c, members in groups.items():
            sub = [byid[m] for m in members]
            genres = [gn for n in sub for gn in n["raw"].get("genres", []) if gn not in dd]
            cnt = defaultdict(int)
            for gn in genres:
                cnt[gn] += 1
            top = sorted(cnt.items(), key=lambda x: -x[1])[:8]
            goty = [n["raw"]["title_zh"] for n in sub if n["raw"].get("is_goty")]
            ratings = [n["raw"].get("player_rating") for n in sub
                       if n["raw"].get("player_rating") not in (None, "")]
            out.append({
                "community": int(c), "size": len(members),
                "goty_members": goty,
                "avg_rating": round(float(np.mean(ratings)), 1) if ratings else 0.0,
                "top_genres": [[k, v] for k, v in top],
                "representative": sorted(sub, key=lambda n: -(n["raw"].get("player_rating") or 0))[0]["raw"]["title_zh"],
            })
        return out


# ==========================================================================
# 热点统计（时代演变）
# ==========================================================================
@Analyzer.register
class HotspotAnalyzer(Analyzer):
    name = "hotspot"

    def analyze(self, ctx: PipelineContext) -> AnalyzerResult:
        res = AnalyzerResult()
        cfg = ctx.config.hotspot
        games = ctx.games
        dd = ctx.config.design_dims
        studio_name = ctx.studio_names
        genre_names = ctx.genre_names
        ERAS = cfg.eras

        def era_of(year):
            for lo, hi, name in ERAS:
                if lo <= year <= hi:
                    return name
            return None

        era_game_count = defaultdict(int)
        era_genre_count = defaultdict(lambda: defaultdict(int))
        for n in games:
            y = n["raw"].get("year")
            e = era_of(y)
            if not e:
                continue
            era_game_count[e] += 1
            for gn in n["raw"].get("genres", []):
                if gn in dd:
                    continue
                era_genre_count[e][gn] += 1

        rows = []
        for lo, hi, name in ERAS:
            total = era_game_count[name]
            for gn in genre_names:
                cnt = era_genre_count[name].get(gn, 0)
                rows.append({"era": name, "genre": gn, "count": cnt,
                             "share": round(cnt / total, 4) if total else 0.0})
        era_df = pd.DataFrame(rows)

        first, last = ERAS[0][2], ERAS[-1][2]
        first_map = {r["genre"]: r["share"] for r in rows if r["era"] == first}
        last_map = {r["genre"]: r["share"] for r in rows if r["era"] == last}
        rising = []
        for gn in genre_names:
            diff = last_map.get(gn, 0) - first_map.get(gn, 0)
            rising.append({"genre": gn, "first_share": first_map.get(gn, 0),
                           "last_share": last_map.get(gn, 0), "delta": round(diff, 4)})
        rising.sort(key=lambda x: -x["delta"])
        rising_up = [r for r in rising if r["delta"] > 0][:10]
        rising_down = [r for r in rising if r["delta"] < 0][:10]

        # 工作室滚动热度
        goty_years = sorted({n["raw"]["year"] for n in games if n["raw"].get("is_goty")})
        studio_window = []
        w = cfg.rolling_window
        for y in goty_years:
            wins = defaultdict(int)
            for n in games:
                if n["raw"].get("is_goty") and y - (w - 1) <= n["raw"]["year"] <= y:
                    sid = n["raw"].get("developer_id")
                    wins[studio_name.get(sid, n["raw"].get("developer"))] += 1
            top = sorted(wins.items(), key=lambda x: -x[1])[:3]
            studio_window.append({"year": y, "top_studios": [[k, v] for k, v in top]})

        timeline = []
        for n in sorted([x for x in games if x["raw"].get("is_goty")], key=lambda x: x["raw"]["year"]):
            timeline.append({"year": n["raw"]["year"], "title_zh": n["raw"]["title_zh"],
                             "tiers": n["raw"].get("tiers", []), "rating": n["raw"].get("player_rating")})

        summary = {
            "eras": [e[2] for e in ERAS],
            "era_game_counts": dict(era_game_count),
            "rising_genres": rising_up,
            "falling_genres": rising_down,
            "studio_rolling_hotness": studio_window,
            "goty_timeline": timeline,
        }
        res.add("hotspot_era", era_df)
        res.add("hotspot_summary", summary)

        up = "、".join(f"{r['genre']}(+{r['delta']:.2f})" for r in rising_up[:6])
        down = "、".join(f"{r['genre']}({r['delta']:.2f})" for r in rising_down[:5])
        res.write(
            "\n## 四、热点统计（时代演变）\n",
            "时代分桶：" + " / ".join(summary["eras"]) + "\n",
            f"- **上升类型**（末代−首代占比差）：{up}\n",
            f"- **下降类型**：{down}\n",
            "\n**工作室滚动热度（近%d年 GOTY 夺冠数 Top3）：**\n" % w,
            "| 年份 | 热门工作室 |",
            "|---|---|",
        )
        for sw in studio_window:
            s = "、".join(f"{k}({v})" for k, v in sw["top_studios"])
            res.write(f"| {sw['year']} | {s} |")
        res.write(f"\n![类型热度]({PNG['hotspot']})\n")
        return res
