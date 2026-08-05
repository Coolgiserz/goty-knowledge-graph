"""IO 与共享计算（去重）。

原先在 factors / community / hotspot / visualize 中反复重复的逻辑
（genre_names、studio_names、build_game_graph）统一收到这里，
并改为读取配置，便于一致地调整行为。
"""
import json
import os
from collections import defaultdict

import networkx as nx

_HERE = os.path.abspath(__file__)
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # .../repo root
DEFAULT_GRAPH_PATH = os.path.join(_REPO, "data", "graph.json")
DEFAULT_OUT_DIR = os.path.join(_REPO, "analysis", "output")


def ensure_out(out_dir: str = DEFAULT_OUT_DIR) -> str:
    os.makedirs(out_dir, exist_ok=True)
    return out_dir


def load_graph(path: str = DEFAULT_GRAPH_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def game_nodes(g: dict) -> list:
    """游戏节点（含 goty 组）。"""
    return [n for n in g["nodes"] if n["group"] in ("game", "goty")]


def genre_names(g: dict, design_dims) -> list:
    """所有玩法叶子类型名（排除设计维度），排序后返回。"""
    return sorted(
        n["raw"]["name"]
        for n in g["nodes"]
        if n["group"] == "genre" and n["raw"]["name"] not in design_dims
    )


def studio_names(g: dict) -> dict:
    """studio_id -> 规范中文名（避免同一家被拆成多个展示串）。"""
    return {
        n["id"]: (n["raw"].get("name_zh") or n["raw"].get("name"))
        for n in g["nodes"]
        if n["group"] == "studio"
    }


def build_game_graph(g: dict, config) -> nx.Graph:
    """游戏-游戏相似投影图（用于拓扑因子与社区发现）。

    边权 = 共享玩法类型(每个 +genre_weight) + 同工作室(+studio_weight)。
    默认排除设计维度连边，以免“开放世界”把所有开放世界游戏连成一个巨型团。
    权重与是否排除设计维度均来自配置。
    """
    games = game_nodes(g)
    GG = nx.Graph()
    for n in games:
        GG.add_node(n["id"], **n)

    gw = config.game_graph.genre_weight
    sw = config.game_graph.studio_weight
    excl = config.design_dims if config.game_graph.exclude_design_dims else set()

    g2g = defaultdict(list)
    for n in games:
        for gn in n["raw"].get("genres", []):
            if gn in excl:
                continue
            g2g[gn].append(n["id"])
    for gn, ids in g2g.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if GG.has_edge(ids[i], ids[j]):
                    GG[ids[i]][ids[j]]["weight"] += gw
                else:
                    GG.add_edge(ids[i], ids[j], weight=gw)

    stud = defaultdict(list)
    for n in games:
        stud[n["raw"].get("developer_id")].append(n["id"])
    for did, ids in stud.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if GG.has_edge(ids[i], ids[j]):
                    GG[ids[i]][ids[j]]["weight"] += sw
                else:
                    GG.add_edge(ids[i], ids[j], weight=sw)
    return GG


def build_full_nx(g: dict) -> nx.Graph:
    """完整异构图（游戏/工作室/类型/奖项 混合节点），用于工作室全局 PageRank。"""
    G = nx.Graph()
    for n in g["nodes"]:
        G.add_node(n["id"], **n)
    for e in g["edges"]:
        G.add_edge(e["from"], e["to"], type=e.get("type"))
    return G
