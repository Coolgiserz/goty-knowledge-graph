"""社区发现：在“游戏-游戏相似投影图”上做 Louvain 社区划分。

与聚类不同，社区发现直接利用图的连边结构（共享玩法类型 / 同工作室），
找出“玩法家族”。同时报告模块度(Modularity)作为划分质量。
输出 communities.csv（每游戏社区标签）与 community_profile.json。
"""
import json
import os
import networkx as nx
import pandas as pd
from collections import defaultdict

from graphio import load_graph, build_game_graph, game_nodes, ensure_out, OUT_DIR, DESIGN_DIMS


def detect(GG):
    communities = nx.community.louvain_communities(GG, weight="weight", seed=42, resolution=1.0)
    mod = nx.community.modularity(GG, communities, weight="weight")
    # 按规模排序，赋稳定社区 id
    comm_sorted = sorted(communities, key=len, reverse=True)
    node2comm = {}
    for cid, members in enumerate(comm_sorted):
        for m in members:
            node2comm[m] = cid
    return node2comm, mod, len(comm_sorted)


def profile(GG, node2comm, g, top_g=8):
    byid = {n["id"]: n for n in g["nodes"]}
    groups = defaultdict(list)
    for nid, c in node2comm.items():
        groups[c].append(nid)
    genre_names = sorted({n["raw"]["name"] for n in g["nodes"]
                          if n["group"] == "genre" and n["raw"]["name"] not in DESIGN_DIMS})
    out = []
    for c, members in groups.items():
        sub = [byid[m] for m in members]
        genres = [gn for n in sub for gn in n["raw"].get("genres", []) if gn not in DESIGN_DIMS]
        cnt = defaultdict(int)
        for gn in genres:
            cnt[gn] += 1
        top = sorted(cnt.items(), key=lambda x: -x[1])[:top_g]
        goty = [n["raw"]["title_zh"] for n in sub if n["raw"].get("is_goty")]
        out.append({
            "community": int(c), "size": len(members),
            "goty_members": goty,
            "avg_rating": round(float(np_mean([n["raw"].get("player_rating") for n in sub if n["raw"].get("player_rating") not in (None, "")])), 1),
            "top_genres": [[k, v] for k, v in top],
            "representative": sorted(sub, key=lambda n: -(n["raw"].get("player_rating") or 0))[0]["raw"]["title_zh"],
        })
    out.sort(key=lambda x: -x["size"])
    return out


def np_mean(vals):
    import numpy as np
    a = [v for v in vals if v is not None]
    return float(np.mean(a)) if a else 0.0


def main():
    g = load_graph()
    GG = build_game_graph(g)
    node2comm, mod, ncomm = detect(GG)
    prof = profile(GG, node2comm, g)

    df = pd.DataFrame([{"game_id": nid, "community": c} for nid, c in node2comm.items()])
    out = ensure_out()
    df.to_csv(os.path.join(out, "communities.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(out, "community_profile.json"), "w", encoding="utf-8") as f:
        json.dump({"n_communities": ncomm, "modularity": round(mod, 4), "profiles": prof},
                  f, ensure_ascii=False, indent=2)
    print(f"[community] n_communities={ncomm} modularity={mod:.4f}; wrote communities.csv + community_profile.json")
    return df, mod, prof


if __name__ == "__main__":
    main()
