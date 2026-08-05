"""可视化：生成 PNG 图片（matplotlib, Agg 后端）。

所有图使用系统 CJK 字体(PingFang SC)以正确渲染中文。
输出到 analysis/output/：
  factor_correlation.png  因子相关热力图
  k_silhouette.png        k 选择（轮廓系数）
  cluster_pca.png         聚类 PCA 二维散点
  cluster_profile.png     各簇玩法类型画像（热力图）
  community_graph.png     社区发现网络图
  hotspot_trend.png       类型热度随时代演变（折线）
  centrality_top.png      中心性 Top 游戏（柱图）
"""
import os
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
import networkx as nx

from graphio import load_graph, build_game_graph, game_nodes, ensure_out, OUT_DIR, DESIGN_DIMS

# ---- 注册 CJK 字体 ----
CJK = None
for f in fm.fontManager.ttflist:
    if f.name == "PingFang SC":
        try:
            fm.fontManager.addfont(f.fname)
            CJK = f.name
        except Exception:
            pass
        break
plt.rcParams["font.family"] = [CJK, "DejaVu Sans", "sans-serif"] if CJK else ["DejaVu Sans", "sans-serif"]
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["figure.dpi"] = 150

PALETTE = ["#e8a33d", "#3a7ca5", "#2e8b57", "#c0392b", "#8e44ad",
           "#16a085", "#d35400", "#2980b9", "#7f8c8d", "#e84393"]


def _save(fig, name):
    path = os.path.join(OUT_DIR, name)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(f"[viz] {name}")
    return path


def factor_correlation(df):
    num = ["player_rating", "year", "n_genres", "has_open_world", "has_coop",
           "has_online", "gg_degree", "gg_pagerank", "gg_betweenness", "gg_clustering",
           "studio_wins", "studio_n_games", "studio_pagerank"]
    corr = df[num].corr()
    labels = [n.replace("gg_", "图·").replace("studio_", "工作室·") for n in num]
    fig, ax = plt.subplots(figsize=(9, 8))
    im = ax.imshow(corr, vmin=-1, vmax=1, cmap="RdBu_r")
    ax.set_xticks(range(len(num))); ax.set_yticks(range(len(num)))
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    for i in range(len(num)):
        for j in range(len(num)):
            ax.text(j, i, f"{corr.iloc[i, j]:.2f}", ha="center", va="center", fontsize=6,
                    color="white" if abs(corr.iloc[i, j]) > 0.5 else "black")
    ax.set_title("因子相关性热力图")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    return _save(fig, "factor_correlation.png")


def k_silhouette(profile):
    scores = {int(k): v for k, v in profile["silhouette"].items()}  # JSON 键被转成字符串
    ks = sorted(scores)
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.plot(ks, [scores[k] for k in ks], "o-", color="#3a7ca5")
    best = int(profile["best_k"])
    ax.scatter([best], [scores[best]], color="#c0392b", zorder=5, s=80)
    ax.set_xlabel("聚类数 k"); ax.set_ylabel("轮廓系数")
    ax.set_title(f"k 选择（最优 k={best}, 轮廓={scores[best]:.3f}）")
    ax.grid(alpha=0.3)
    return _save(fig, "k_silhouette.png")


def cluster_pca(df, profile):
    fig, ax = plt.subplots(figsize=(8, 6))
    best_k = profile["best_k"]
    for c in range(best_k):
        sub = df[df["cluster"] == c]
        ax.scatter(sub["pca_x"], sub["pca_y"], s=38, alpha=0.8,
                   color=PALETTE[c % len(PALETTE)], label=f"簇{c} (n={len(sub)})")
    # 标注 GOTY
    goty = df[df["is_goty"] == 1]
    ax.scatter(goty["pca_x"], goty["pca_y"], marker="*", s=160, color="#e8a33d",
               edgecolor="black", linewidth=0.6, label="年度最佳", zorder=5)
    ax.set_xlabel("PCA-1"); ax.set_ylabel("PCA-2")
    ax.set_title("游戏聚类（PCA 二维投影，★=年度最佳）")
    ax.legend(fontsize=8, loc="best")
    return _save(fig, "cluster_pca.png")


def cluster_profile_heatmap(profile, genre_names):
    profs = profile["profiles"]
    # 收集各簇 top genres 的并集
    gset = []
    for p in profs:
        for gn, _ in p["top_genres"]:
            if gn not in gset:
                gset.append(gn)
    # 限制前 12 个最“有区分度”的类型（按簇间方差）
    mat = np.array([[dict(p["top_genres"]).get(gn, 0.0) for gn in gset] for p in profs])
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
    return _save(fig, "cluster_profile.png")


def community_graph(g, communities):
    GG = build_game_graph(g)
    cmap = {str(r.game_id): int(r.community) for _, r in communities.iterrows()}
    # 只画边权 >= 2 的子图，避免全连接过密
    SG = nx.Graph()
    for u, v, d in GG.edges(data=True):
        if d["weight"] >= 2:
            SG.add_edge(u, v, weight=d["weight"])
    for nid in GG.nodes():
        SG.add_node(nid)
    pos = nx.spring_layout(SG, seed=42, k=0.6)
    fig, ax = plt.subplots(figsize=(11, 9))
    node_colors = [PALETTE[cmap.get(n, 0) % len(PALETTE)] for n in SG.nodes()]
    sizes = []
    byid = {n["id"]: n for n in g["nodes"]}
    for n in SG.nodes():
        sz = 90 if byid[n]["raw"].get("is_goty") else 28
        sizes.append(sz)
    nx.draw_networkx_nodes(SG, pos, node_color=node_colors, node_size=sizes, ax=ax, alpha=0.9)
    # 仅画较粗的边，避免视觉混乱
    ew = [d["weight"] for _, _, d in SG.edges(data=True)]
    nx.draw_networkx_edges(SG, pos, width=[min(2.0, w * 0.3) for w in ew],
                           alpha=0.18, ax=ax, edge_color="#888")
    # 标注 GOTY 节点
    labels = {n: byid[n]["raw"]["title"] for n in SG.nodes() if byid[n]["raw"].get("is_goty")}
    nx.draw_networkx_labels(SG, pos, labels=labels, font_size=7, ax=ax,
                            font_color="#111", font_weight="bold")
    ax.set_title("社区发现：游戏玩法家族（Louvain，★=年度最佳，色=社区）")
    ax.axis("off")
    return _save(fig, "community_graph.png")


def hotspot_trend(summary):
    era_df = pd.read_csv(os.path.join(OUT_DIR, "hotspot_era.csv"))
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
    return _save(fig, "hotspot_trend.png")


def centrality_top(df):
    top = df.sort_values("gg_pagerank", ascending=False).head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 6))
    labels = [t if t else z for t, z in zip(top["title"], top["title_zh"])]
    colors = ["#e8a33d" if g else "#3a7ca5" for g in top["is_goty"]]
    ax.barh(range(len(top)), top["gg_pagerank"].values, color=colors)
    ax.set_yticks(range(len(top))); ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel("游戏投影图 PageRank")
    ax.set_title("玩法关系网中心性 Top 15（金=年度最佳）")
    return _save(fig, "centrality_top.png")


def main():
    g = load_graph()
    df = pd.read_csv(os.path.join(OUT_DIR, "factors.csv"))
    clusters = pd.read_csv(os.path.join(OUT_DIR, "clusters.csv"))
    communities = pd.read_csv(os.path.join(OUT_DIR, "communities.csv"))
    profile = json.load(open(os.path.join(OUT_DIR, "cluster_profile.json"), encoding="utf-8"))
    summary = json.load(open(os.path.join(OUT_DIR, "hotspot_summary.json"), encoding="utf-8"))

    factor_correlation(df)
    k_silhouette(profile)
    cluster_pca(clusters, profile)
    cluster_profile_heatmap(profile, None)
    community_graph(g, communities)
    hotspot_trend(summary)
    centrality_top(df)
    print("[viz] all PNGs generated.")


if __name__ == "__main__":
    main()
