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
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.cluster import KMeans, AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity
from scipy.cluster.hierarchy import linkage, fcluster

from .context import PipelineContext
from .clusterers import Clusterer
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

        cols = _style_cols(df)
        X = df[cols].astype(float).values
        Xs = StandardScaler().fit_transform(X)  # 标准化，避免量纲/one-hot 主导

        # 每家工作室 = 其作品风格向量的均值（单作品工作室即该作向量）
        by_studio = defaultdict(list)
        for i, did in enumerate(df["developer_id"]):
            by_studio[did].append(Xs[i])
        studio_ids = sorted(by_studio.keys())
        S = np.array([np.mean(by_studio[d], axis=0) for d in studio_ids])

        # 余弦相似度（方向一致即风格接近，与长度无关）
        sim = cosine_similarity(S)

        # 工作室风格聚类（在标准化向量上做层次 Ward，簇数 ~ ceil(sqrt(n))）
        n_stu = len(studio_ids)
        k_stu = max(2, min(6, int(np.ceil(np.sqrt(n_stu)))))
        Z = linkage(S, method="ward")
        labels = fcluster(Z, k_stu, criterion="maxclust")

        # PCA 二维用于散点可视化
        pcs = PCA(n_components=2, random_state=cfg.random_state).fit_transform(S)

        style_df = pd.DataFrame({
            "studio_id": studio_ids,
            "studio": [studio_name.get(s, s) for s in studio_ids],
            "n_games": [len(by_studio[s]) for s in studio_ids],
            "pca_x": pcs[:, 0], "pca_y": pcs[:, 1],
            "style_cluster": labels,
        })

        # 最相似工作室对（去对角，取 Top8）
        top_pairs = []
        for i in range(n_stu):
            for j in range(i + 1, n_stu):
                top_pairs.append((studio_ids[i], studio_ids[j], float(sim[i, j])))
        top_pairs.sort(key=lambda x: -x[2])
        top_pairs = top_pairs[:8]

        # 工作室风格簇分组
        clusters = defaultdict(list)
        for sid, lab in zip(studio_ids, labels):
            clusters[int(lab)].append(studio_name.get(sid, sid))
        cluster_groups = [{"cluster": c, "studios": names}
                          for c, names in sorted(clusters.items())]

        res.add("studio_style", style_df)
        res.add("studio_sim_matrix", {"studio_ids": studio_ids,
                                      "studio_names": [studio_name.get(s, s) for s in studio_ids],
                                      "matrix": sim})
        res.add("studio_style_summary",
                {"n_studios": n_stu, "k_style": k_stu,
                 "top_pairs": [[studio_name.get(a, a), studio_name.get(b, b), round(s, 3)]
                               for a, b, s in top_pairs],
                 "clusters": cluster_groups})

        res.write(
            "\n## 五、开发商游戏风格相似性\n",
            "把每家工作室映射为其作品风格向量的均值，在标准化因子空间（玩法拓扑+属性+类型 one-hot，"
            "**不含声誉列**）上计算余弦相似度——衡量「两家厂牌做出的游戏是否一脉相承」。\n",
            f"共 **{n_stu} 家**工作室，风格层次聚类得 **{k_stu} 组**：\n",
            "| 风格簇 | 成员工作室 |",
            "|---|---|",
        )
        for g in cluster_groups:
            res.write(f"| 簇{g['cluster']} | {'、'.join(g['studios'])} |")
        res.write(
            "\n**最相似的工作室对（Top8）：**\n",
            "| 工作室 A | 工作室 B | 风格余弦相似度 |",
            "|---|---|---|",
        )
        for a, b, s in res.artifacts["studio_style_summary"]["top_pairs"]:
            res.write(f"| {a} | {b} | {s:.3f} |")
        res.write(
            f"\n> 注：相似度高代表两家厂牌的游戏在玩法类型/设计维度上高度重合"
            "（如都爱做开放世界动作），可作为「厂牌基因」的量化证据；单作品工作室的向量即其唯一作品。\n",
            f"![工作室风格相似度]({PNG['studio_sim']})\n",
            f"![工作室风格散点]({PNG['studio_style_scatter']})\n",
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
