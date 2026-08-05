"""聚类：对游戏因子矩阵做 KMeans + 层次聚类。

聚类特征 X = 标准化后的[属性因子 + 图拓扑因子 + 声誉因子 + 类型 one-hot]。
用轮廓系数在 k=2..8 中选优；同时给出层次聚类(Ward)作为对照。
输出 clusters.csv（每游戏聚类标签）与 cluster_profile.json（簇画像）。
"""
import json
import os
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import KMeans, AgglomerativeClustering
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score

from graphio import ensure_out, OUT_DIR, DESIGN_DIMS


def clustering_features(df, genre_names):
    base = ["player_rating", "year", "n_genres",
            "has_open_world", "has_coop", "has_online",
            "studio_wins", "studio_n_games",
            "gg_pagerank", "gg_betweenness"]
    gcols = [f"g_{gn}" for gn in genre_names]
    cols = base + gcols
    X = df[cols].copy()
    # 缺失评分用中位数填补（部分“其他作品”无 Metacritic 分）
    if X["player_rating"].isna().any():
        X["player_rating"] = X["player_rating"].fillna(X["player_rating"].median())
    # 年份归一化到 0-1；对数变换压缩中心性长尾
    X["year"] = (X["year"] - df["year"].min()) / (df["year"].max() - df["year"].min())
    X["gg_pagerank"] = np.log1p(X["gg_pagerank"] * 1e4)
    X["gg_betweenness"] = np.log1p(X["gg_betweenness"] * 1e4)
    return X, cols


def select_k(X, k_range=range(2, 9)):
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)
    scores = {}
    for k in k_range:
        km = KMeans(n_clusters=k, random_state=42, n_init=10)
        lab = km.fit_predict(Xs)
        if len(set(lab)) > 1:
            scores[k] = float(silhouette_score(Xs, lab))
    best_k = max(scores, key=scores.get)
    return scores, best_k


def profile_clusters(df, labels, genre_names, top_g=8):
    out = []
    for c in sorted(set(labels)):
        sub = df[labels == c]
        prof = {"cluster": int(c), "size": int(len(sub)),
                "goty": int(sub["is_goty"].sum()),
                "goty_rate": round(float(sub["is_goty"].mean()), 3),
                "avg_rating": round(float(sub["player_rating"].mean()), 1) if sub["player_rating"].notna().any() else None,
                "avg_year": round(float(sub["year"].mean()), 1),
                "top_games": sub.sort_values("player_rating", ascending=False)["title_zh"].head(5).tolist(),
                "top_genres": []}
        gcols = [f"g_{gn}" for gn in genre_names]
        means = sub[gcols].mean().sort_values(ascending=False)
        for col, v in means.head(top_g).items():
            prof["top_genres"].append((col[2:], round(float(v), 3)))
        out.append(prof)
    return out


def main(df=None, genre_names=None):
    if df is None or genre_names is None:
        from factors import main as fmain
        df, genre_names, _ = fmain()
    X, cols = clustering_features(df, genre_names)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X)

    scores, best_k = select_k(X)
    km = KMeans(n_clusters=best_k, random_state=42, n_init=10)
    labels = km.fit_predict(Xs)
    df = df.copy()
    df["cluster"] = labels

    # 层次聚类对照
    agg = AgglomerativeClustering(n_clusters=best_k)
    df["cluster_hier"] = agg.fit_predict(Xs)

    # PCA 2D（供可视化）
    pca = PCA(n_components=2)
    coords = pca.fit_transform(Xs)
    df["pca_x"] = coords[:, 0]
    df["pca_y"] = coords[:, 1]

    profiles = profile_clusters(df, labels, genre_names)

    out = ensure_out()
    df.to_csv(os.path.join(out, "clusters.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(out, "cluster_profile.json"), "w", encoding="utf-8") as f:
        json.dump({"best_k": best_k, "silhouette": scores, "profiles": profiles},
                  f, ensure_ascii=False, indent=2)
    print(f"[cluster] best_k={best_k} silhouette={scores[best_k]:.3f}; wrote clusters.csv + cluster_profile.json")
    return df, best_k, scores, profiles


if __name__ == "__main__":
    main()
