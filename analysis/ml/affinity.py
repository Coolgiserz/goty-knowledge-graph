"""GOTY 品味网络（个性化随机游走 / Personalized PageRank）。

把全部「年度最佳（GOTY）获奖作」作为种子，在完整异构图（游戏↔类型↔工作室↔奖项）
上做个性化 PageRank：某节点得分越高，代表它在「年度最佳品味」网络中越中心，
即「喜欢 GOTY 的人，还会喜欢谁」。

这是 GotyAffinityAnalyzer 与 API 探索板块共用的可复用计算模块。
注意：该分数与工作室在图中的体量 / GOTY 种子数高度相关，应作为
「工作室声望代理」来解读，而非纯粹的玩法相似度——详见批判性反思文档。
"""
import networkx as nx
from collections import defaultdict


def goty_pagerank(Gfull, games, studio_name, alpha=0.85, top_n=10):
    """从全部 GOTY 种子做个性化 PageRank，返回推荐 / 工作室亲和力。

    参数
    ----
    Gfull       : nx.Graph  完整异构图
    games       : list       游戏节点列表（含 "id" / "raw"）
    studio_name : dict       developer_id -> 工作室中文名
    alpha       : float      PageRank 阻尼系数
    top_n       : int        推荐 / 榜单条数

    返回 dict：{n_seeds, alpha, top_games, top_studios, seed_scores}
    """
    goty_ids = [n["id"] for n in games if n["raw"].get("is_goty")]
    if not goty_ids:
        return {"n_seeds": 0, "alpha": alpha, "top_games": [],
                "top_studios": [], "seed_scores": []}
    seeds = set(goty_ids)
    pers = {gid: 1.0 / len(goty_ids) for gid in goty_ids}
    pr = nx.pagerank(Gfull, personalization=pers, alpha=alpha, max_iter=300)

    rec = []
    for n in games:
        if n["id"] in seeds:
            continue
        rec.append({
            "game_id": n["id"],
            "title_zh": n["raw"].get("title_zh") or n["raw"].get("title"),
            "studio": studio_name.get(n["raw"].get("developer_id"),
                                      n["raw"].get("developer")),
            "is_goty": False,
            "affinity": round(float(pr.get(n["id"], 0.0)), 5),
        })
    rec.sort(key=lambda x: -x["affinity"])
    top_games = rec[:top_n]

    sp = defaultdict(float)
    for n in games:
        sp[n["raw"].get("developer_id")] += float(pr.get(n["id"], 0.0))
    top_studios = [{"studio": studio_name.get(d, d), "affinity": round(v, 5)}
                   for d, v in sorted(sp.items(), key=lambda kv: -kv[1])[:top_n]]

    seed_scores = [{
        "title_zh": n["raw"].get("title_zh") or n["raw"].get("title"),
        "studio": studio_name.get(n["raw"].get("developer_id"),
                                  n["raw"].get("developer")),
        "affinity": round(float(pr.get(n["id"], 0.0)), 5),
    } for n in games if n["id"] in seeds]

    return {"n_seeds": len(goty_ids), "alpha": alpha,
            "top_games": top_games, "top_studios": top_studios,
            "seed_scores": seed_scores}
