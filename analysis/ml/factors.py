"""高频因子（特征工程）。

把每张游戏节点当作一个“资产”，从知识图谱中派生出一张“宽因子表”：
  - 图拓扑因子：在“游戏-游戏相似投影图”上的度 / PageRank / 介数 / 聚类系数
    （衡量该游戏在玩法关系网中的中心性与桥接作用）
  - 属性因子：评分、发行年份（归一化）、所属玩法类型数、是否开放世界/合作/在线
  - 声誉因子：所在工作室的年度最佳夺冠数、工作室作品总数、工作室 PageRank
  - 类型 one-hot：每个玩法叶子类型一列（0/1）

输出的 factors.csv 是后续聚类 / 社区发现 / 热点统计的统一输入。
（注：原数据没有“日内 tick”级别时间序列，年份是可用的最细时间粒度，
因此“高频”在此指代“从图结构派生的大量细粒度截面因子”，而非高频时序。）
"""
import json
import os
import numpy as np
import pandas as pd
import networkx as nx
from collections import defaultdict

from graphio import load_graph, build_nx, build_game_graph, game_nodes, DESIGN_DIMS, ensure_out, OUT_DIR


def compute_factors(g, G=None, GG=None):
    games = game_nodes(g)
    byid = {n["id"]: n for n in g["nodes"]}
    if G is None:
        from graphio import build_nx as _b
        G = _b(g)
    if GG is None:
        GG = build_game_graph(g)

    # ---- 工作室声誉 ----
    studio_wins = defaultdict(int)
    studio_games = defaultdict(int)
    for n in games:
        did = n["raw"].get("developer_id")
        studio_games[did] += 1
        if n["raw"].get("is_goty"):
            studio_wins[did] += 1

    # ---- 图中心性（在游戏投影图上）----
    pr = nx.pagerank(GG, alpha=0.85)
    bc = nx.betweenness_centrality(GG)
    cl = nx.clustering(GG)
    deg = dict(GG.degree())
    # 工作室在全图的 PageRank（声誉强度）
    pr_full = nx.pagerank(G, alpha=0.85)

    # ---- 玩法类型列（排除设计维度）----
    genre_names = sorted({n["raw"]["name"] for n in g["nodes"]
                          if n["group"] == "genre" and n["raw"]["name"] not in DESIGN_DIMS})

    rows = []
    for n in games:
        r = n["raw"]
        did = r.get("developer_id")
        genres = r.get("genres", [])
        play_genres = [x for x in genres if x not in DESIGN_DIMS]
        rating = r.get("player_rating")
        row = {
            "game_id": n["id"],
            "title": r.get("title"),
            "title_zh": r.get("title_zh"),
            "year": r.get("year"),
            "player_rating": rating if rating not in (None, "") else np.nan,
            "is_goty": 1 if r.get("is_goty") else 0,
            "developer_id": did,
            "developer": r.get("developer"),
            "n_genres": len(play_genres),
            "has_open_world": 1 if "开放世界" in genres else 0,
            "has_coop": 1 if "多人合作" in genres else 0,
            "has_online": 1 if "在线" in genres else 0,
            # 图拓扑因子（游戏投影图）
            "gg_degree": deg.get(n["id"], 0),
            "gg_pagerank": pr.get(n["id"], 0.0),
            "gg_betweenness": bc.get(n["id"], 0.0),
            "gg_clustering": cl.get(n["id"], 0.0),
            # 声誉因子
            "studio_wins": studio_wins.get(did, 0),
            "studio_n_games": studio_games.get(did, 0),
            "studio_pagerank": pr_full.get(did, 0.0),
        }
        for gn in genre_names:
            row[f"g_{gn}"] = 1 if gn in genres else 0
        rows.append(row)

    df = pd.DataFrame(rows)
    factor_doc = build_factor_doc(df, genre_names)
    return df, genre_names, factor_doc


def build_factor_doc(df, genre_names):
    doc = [
        ("player_rating", "数值", "Metacritic 媒体均分（0-100）"),
        ("year", "数值", "发行/获奖年份"),
        ("n_genres", "数值", "所属玩法原子类型数量"),
        ("has_open_world", "0/1", "是否开放世界（设计维度）"),
        ("has_coop", "0/1", "是否多人合作（设计维度）"),
        ("has_online", "0/1", "是否在线（设计维度）"),
        ("gg_degree", "数值", "游戏投影图中的度数（相似玩法邻居数）"),
        ("gg_pagerank", "数值", "游戏投影图 PageRank（玩法网中心性）"),
        ("gg_betweenness", "数值", "游戏投影图介数（桥接不同玩法社区的程度）"),
        ("gg_clustering", "数值", "游戏投影图聚类系数（邻居间的紧密度）"),
        ("studio_wins", "数值", "工作室年度最佳夺冠次数（声誉）"),
        ("studio_n_games", "数值", "工作室在数据集中的作品总数（产出）"),
        ("studio_pagerank", "数值", "工作室在全图的 PageRank（声誉强度）"),
    ]
    for gn in genre_names:
        doc.append((f"g_{gn}", "0/1", f"是否属于玩法类型「{gn}」"))
    return doc


def main():
    g = load_graph()
    df, genre_names, factor_doc = compute_factors(g)
    out = ensure_out()
    df.to_csv(os.path.join(out, "factors.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(out, "factor_doc.json"), "w", encoding="utf-8") as f:
        json.dump(factor_doc, f, ensure_ascii=False, indent=2)
    print(f"[factors] wrote factors.csv ({df.shape[0]} games x {df.shape[1]} cols), {len(genre_names)} genre columns")
    return df, genre_names, factor_doc


if __name__ == "__main__":
    main()
