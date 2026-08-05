"""可视化（Visualizer）：所有图表从 ctx.artifacts 读取，不再经磁盘中转。

每个可视化是一个 Visualizer 子类，通过 @Visualizer.register 注册；
pipeline 逐个调用 render(ctx)，生成 PNG 到 ctx.out_dir。

新增一张图只需写子类并注册——即「可插拔」。
"""
import os
import json
from collections import defaultdict

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
import networkx as nx

from .context import PipelineContext
from .constants import PNG

# 不参与相关分析 / 聚类的标识列
_IDENT = {"game_id", "title", "title_zh", "developer_id", "developer", "is_goty"}

# ---- 注册 CJK 字体（PingFang SC），保证中文正常渲染 ----
_CJK = None
for _f in fm.fontManager.ttflist:
    if _f.name == "PingFang SC":
        try:
            fm.fontManager.addfont(_f.fname)
            _CJK = _f.name
        except Exception:
            pass
        break
plt.rcParams["font.family"] = [_CJK, "DejaVu Sans", "sans-serif"] if _CJK else ["DejaVu Sans", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150

PALETTE = ["#e8a33d", "#3a7ca5", "#2e8b57", "#c0392b", "#8e44ad",
           "#16a085", "#d35400", "#2980b9", "#7f8c8d", "#e84393"]


class Visualizer:
    name = "base"
    filename = None
    _registry: dict = {}

    def render(self, ctx: PipelineContext) -> str:
        raise NotImplementedError

    @classmethod
    def register(cls, sub):
        cls._registry[sub.name] = sub
        return sub

    @classmethod
    def all(cls):
        return list(cls._registry.values())


def _save(fig, name, out_dir):
    path = os.path.join(out_dir, name)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


# --------------------------------------------------------------------------
@Visualizer.register
class FactorCorrelationViz(Visualizer):
    name = "factor_corr"
    filename = PNG["factor_corr"]

    def render(self, ctx: PipelineContext) -> str:
        df = ctx.get("factors")
        num = [c for c in df.columns if c not in _IDENT and not c.startswith("g_")]
        corr = df[num].corr()
        labels = [n.replace("gg_", "图·").replace("studio_", "工作室·").replace("has_", "")
                  for n in num]
        fig, ax = plt.subplots(figsize=(9, 8))
        im = ax.imshow(corr, vmin=-1, vmax=1, cmap="RdBu_r")
        ax.set_xticks(range(len(num))); ax.set_yticks(range(len(num)))
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        ax.set_yticklabels(labels, fontsize=8)
        for i in range(len(num)):
            for j in range(len(num)):
                ax.text(j, i, f"{corr.iloc[i, j]:.2f}", ha="center", va="center", fontsize=6,
                        color="white" if abs(corr.iloc[i, j]) > 0.5 else "black")
        ax.set_title("因子相关性热力图（识别多重共线）")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class KSihouetteViz(Visualizer):
    name = "k_silhouette"
    filename = PNG["k_silhouette"]

    def render(self, ctx: PipelineContext) -> str:
        profile = ctx.get("cluster_profile")
        scores = {int(k): v for k, v in profile.get("silhouette", {}).items()}
        if not scores:
            return None
        ks = sorted(scores)
        fig, ax = plt.subplots(figsize=(7, 4.5))
        ax.plot(ks, [scores[k] for k in ks], "o-", color="#3a7ca5")
        best = int(profile["best_k"])
        if best in scores:
            ax.scatter([best], [scores[best]], color="#c0392b", zorder=5, s=80)
        ax.set_xlabel("聚类数 k"); ax.set_ylabel("轮廓系数")
        ax.set_title(f"k 选择（{profile.get('method','')} 最优 k={best}）")
        ax.grid(alpha=0.3)
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class ClusterPCAViz(Visualizer):
    name = "cluster_pca"
    filename = PNG["cluster_pca"]

    def render(self, ctx: PipelineContext) -> str:
        df = ctx.get("clusters")
        profile = ctx.get("cluster_profile")
        best_k = profile["best_k"]
        fig, ax = plt.subplots(figsize=(8, 6))
        labels = sorted({l for l in df["cluster"] if l >= 0})
        for c in labels:
            sub = df[df["cluster"] == c]
            ax.scatter(sub["pca_x"], sub["pca_y"], s=38, alpha=0.8,
                       color=PALETTE[c % len(PALETTE)], label=f"簇{c} (n={len(sub)})")
        goty = df[df["is_goty"] == 1]
        if len(goty):
            ax.scatter(goty["pca_x"], goty["pca_y"], marker="*", s=160, color="#e8a33d",
                       edgecolor="black", linewidth=0.6, label="年度最佳", zorder=5)
        ax.set_xlabel("PCA-1"); ax.set_ylabel("PCA-2")
        ax.set_title(f"游戏聚类（PCA 二维投影，★=年度最佳，共{len(labels)}簇）")
        ax.legend(fontsize=8, loc="best")
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class ClusterProfileViz(Visualizer):
    name = "cluster_profile"
    filename = PNG["cluster_profile"]

    def render(self, ctx: PipelineContext) -> str:
        profile = ctx.get("cluster_profile")
        profs = profile["profiles"]
        gset = []
        for p in profs:
            for gn, _ in p["top_genres"]:
                if gn not in gset:
                    gset.append(gn)
        mat = np.array([[dict(p["top_genres"]).get(gn, 0.0) for gn in gset] for p in profs])
        if mat.size == 0:
            return None
        var = mat.var(axis=0)
        order = np.argsort(-var)[:12]
        gset2 = [gset[i] for i in order]
        mat2 = mat[:, order]
        fig, ax = plt.subplots(figsize=(10, max(3, 0.6 * len(profs) + 1.5)))
        im = ax.imshow(mat2, cmap="YlGnBu", aspect="auto")
        ax.set_xticks(range(len(gset2))); ax.set_xticklabels(gset2, rotation=45, ha="right", fontsize=8)
        ax.set_yticks(range(len(profs)))
        ax.set_yticklabels([f'簇{p["cluster"]} (n={p["size"]}, ★{p["goty"]})' for p in profs], fontsize=8)
        for i in range(mat2.shape[0]):
            for j in range(mat2.shape[1]):
                ax.text(j, i, f"{mat2[i, j]:.2f}", ha="center", va="center", fontsize=7,
                        color="white" if mat2[i, j] > 0.5 else "black")
        ax.set_title("各聚类玩法类型画像（类型占比均值）")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class CommunityGraphViz(Visualizer):
    name = "community"
    filename = PNG["community"]

    def render(self, ctx: PipelineContext) -> str:
        GG = ctx.GG
        node2comm = ctx.get("community_map")
        if node2comm is None:
            return None
        SG = nx.Graph()
        for u, v, d in GG.edges(data=True):
            if d["weight"] >= 2:
                SG.add_edge(u, v, weight=d["weight"])
        for nid in GG.nodes():
            SG.add_node(nid)
        pos = nx.spring_layout(SG, seed=42, k=0.6)
        byid = {n["id"]: n for n in ctx.graph["nodes"]}
        fig, ax = plt.subplots(figsize=(11, 9))
        node_colors = [PALETTE[node2comm.get(n, 0) % len(PALETTE)] for n in SG.nodes()]
        sizes = [90 if byid[n]["raw"].get("is_goty") else 28 for n in SG.nodes()]
        nx.draw_networkx_nodes(SG, pos, node_color=node_colors, node_size=sizes, ax=ax, alpha=0.9)
        ew = [d["weight"] for _, _, d in SG.edges(data=True)]
        nx.draw_networkx_edges(SG, pos, width=[min(2.0, w * 0.3) for w in ew],
                               alpha=0.18, ax=ax, edge_color="#888")
        labels = {n: byid[n]["raw"]["title"] for n in SG.nodes() if byid[n]["raw"].get("is_goty")}
        nx.draw_networkx_labels(SG, pos, labels=labels, font_size=7, ax=ax,
                                font_color="#111", font_weight="bold")
        ax.set_title("社区发现：游戏玩法家族（Louvain，★=年度最佳，色=社区）")
        ax.axis("off")
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class HotspotTrendViz(Visualizer):
    name = "hotspot"
    filename = PNG["hotspot"]

    def render(self, ctx: PipelineContext) -> str:
        summary = ctx.get("hotspot_summary")
        era_df = ctx.get("hotspot_era")
        if summary is None or era_df is None:
            return None
        pick = [r["genre"] for r in summary["rising_genres"][:6]] + \
               [r["genre"] for r in summary["falling_genres"][:3]]
        pick = list(dict.fromkeys(pick))
        eras = summary["eras"]
        fig, ax = plt.subplots(figsize=(9, 5.5))
        for i, gn in enumerate(pick):
            sub = era_df[era_df["genre"] == gn].set_index("era").reindex(eras)
            ax.plot(eras, sub["share"].values * 100, "o-",
                    color=PALETTE[i % len(PALETTE)], label=gn)
        ax.set_xlabel("时代"); ax.set_ylabel("类型占比 (%)")
        ax.set_title("类型热度演变（上升 vs 下降类型）")
        ax.legend(fontsize=8, loc="best")
        ax.grid(alpha=0.3)
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class CentralityTopViz(Visualizer):
    name = "centrality"
    filename = PNG["centrality"]

    def render(self, ctx: PipelineContext) -> str:
        df = ctx.get("factors")
        top = df.sort_values("gg_pagerank", ascending=False).head(15).iloc[::-1]
        fig, ax = plt.subplots(figsize=(8, 6))
        labels = [t if t else z for t, z in zip(top["title"], top["title_zh"])]
        colors = ["#e8a33d" if g else "#3a7ca5" for g in top["is_goty"]]
        ax.barh(range(len(top)), top["gg_pagerank"].values, color=colors)
        ax.set_yticks(range(len(top))); ax.set_yticklabels(labels, fontsize=8)
        ax.set_xlabel("游戏投影图 PageRank")
        ax.set_title("玩法关系网中心性 Top 15（金=年度最佳）")
        return _save(fig, self.filename, ctx.out_dir)


# --------------------------------------------------------------------------
@Visualizer.register
class StudioSimilarityViz(Visualizer):
    name = "studio_sim"
    filename = PNG["studio_sim"]

    def render(self, ctx: PipelineContext) -> str:
        blob = ctx.get("studio_sim_matrix")
        if blob is None:
            return None
        names = blob["studio_names"]
        M = np.array(blob["matrix"])
        fig, ax = plt.subplots(figsize=(10, 9))
        im = ax.imshow(M, vmin=-1, vmax=1, cmap="RdBu_r")
        ax.set_xticks(range(len(names))); ax.set_yticks(range(len(names)))
        ax.set_xticklabels(names, rotation=45, ha="right", fontsize=8)
        ax.set_yticklabels(names, fontsize=8)
        for i in range(len(names)):
            for j in range(len(names)):
                v = M[i, j]
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=6,
                        color="white" if abs(v) > 0.5 else "black")
        ax.set_title("开发商游戏风格余弦相似度（排除声誉列）")
        fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class StudioStyleScatterViz(Visualizer):
    name = "studio_style_scatter"
    filename = PNG["studio_style_scatter"]

    def render(self, ctx: PipelineContext) -> str:
        df = ctx.get("studio_style")
        if df is None:
            return None
        fig, ax = plt.subplots(figsize=(9, 8))
        for c in sorted(df["style_cluster"].unique()):
            sub = df[df["style_cluster"] == c]
            ax.scatter(sub["pca_x"], sub["pca_y"], s=120, alpha=0.8,
                       color=PALETTE[int(c) % len(PALETTE)], label=f"风格簇{c}")
        for _, r in df.iterrows():
            ax.annotate(r["studio"], (r["pca_x"], r["pca_y"]), fontsize=7,
                        xytext=(4, 3), textcoords="offset points")
        ax.set_xlabel("风格 PCA-1"); ax.set_ylabel("风格 PCA-2")
        ax.set_title("开发商风格空间（点=工作室，色=风格簇）")
        ax.legend(fontsize=8, loc="best")
        ax.grid(alpha=0.3)
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class GotyDistinguishViz(Visualizer):
    name = "goty_distinguish"
    filename = PNG["goty_distinguish"]

    def render(self, ctx: PipelineContext) -> str:
        blob = ctx.get("goty_profile")
        if blob is None:
            return None
        top = blob["factors"][:12][::-1]  # 横向条形，最大在上方
        labels = [f["factor"] for f in top]
        vals = [f["cohen_d"] for f in top]
        colors = ["#c0392b" if v > 0 else "#3a7ca5" for v in vals]
        fig, ax = plt.subplots(figsize=(8, 6))
        ax.barh(range(len(top)), vals, color=colors)
        ax.set_yticks(range(len(top))); ax.set_yticklabels(labels, fontsize=8)
        ax.axvline(0, color="black", linewidth=0.8)
        ax.set_xlabel("Cohen's d（红=GOTY 更高，蓝=其他更高）")
        ax.set_title("年度最佳 vs 其他作品：区分度最高的因子")
        ax.grid(alpha=0.3, axis="x")
        return _save(fig, self.filename, ctx.out_dir)


@Visualizer.register
class GotyGenreViz(Visualizer):
    name = "goty_genre"
    filename = PNG["goty_genre"]

    def render(self, ctx: PipelineContext) -> str:
        df = ctx.get("goty_genre")
        if df is None:
            return None
        d = df[df["overindex"].notna()].sort_values("overindex", ascending=False).head(12)
        if d.empty:
            return None
        labels = d["genre"].tolist()
        vals = d["overindex"].tolist()
        plot_vals = [min(v, 5.0) for v in vals]  # 极端值(仅GOTY有)截断以保可读性
        colors = ["#e8a33d" if v >= 1 else "#7f8c8d" for v in vals]
        fig, ax = plt.subplots(figsize=(8, 6.5))
        ax.barh(range(len(d)), plot_vals, color=colors)
        ax.set_yticks(range(len(d))); ax.set_yticklabels(labels, fontsize=8)
        ax.axvline(1.0, color="black", linestyle="--", linewidth=0.8)
        for i, v in enumerate(vals):
            ax.text(plot_vals[i] + 0.05, i, f"{v}", va="center", fontsize=7)
        ax.set_xlabel("类型 Over-index = GOTY 中占比 / 全体占比（≥1 即 GOTY 偏爱）")
        ax.set_title("GOTY 偏爱的玩法类型（Over-index Top12）")
        ax.invert_yaxis()
        ax.grid(alpha=0.3, axis="x")
        return _save(fig, self.filename, ctx.out_dir)
