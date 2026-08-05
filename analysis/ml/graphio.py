"""共享工具：加载 graph.json、构建 networkx 图、构建游戏-游戏相似投影图。

输入数据：仓库根目录 data/graph.json（由 src/build.py 生成的知识图谱）。
所有 ML 脚本把结果写到 analysis/output/。
"""
import json
import os
from collections import defaultdict

import networkx as nx

_HERE = os.path.abspath(__file__)
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))  # .../repo root
GRAPH_PATH = os.path.join(_REPO, "data", "graph.json")
OUT_DIR = os.path.join(_REPO, "analysis", "output")
DESIGN_DIMS = {"开放世界", "多人合作", "在线"}  # 设计维度（叠加于玩法类别之上）


def ensure_out():
    os.makedirs(OUT_DIR, exist_ok=True)
    return OUT_DIR


def load_graph(path=GRAPH_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_nx(g):
    """构建完整异构图（游戏/工作室/类型/奖项 混合节点）。"""
    G = nx.Graph()
    for n in g["nodes"]:
        G.add_node(n["id"], **n)
    for e in g["edges"]:
        G.add_edge(e["from"], e["to"], type=e.get("type"))
    return G


def game_nodes(g):
    return [n for n in g["nodes"] if n["group"] in ("game", "goty")]


def build_game_graph(g):
    """游戏-游戏相似投影图（用于中心性因子与社区发现）。

    边权 = 共享玩法类型(每个 +1) + 同工作室(+4)。不含设计维度的共享边，
    以免“开放世界”把所有开放世界游戏连成一个巨大团。
    """
    games = game_nodes(g)
    GG = nx.Graph()
    for n in games:
        GG.add_node(n["id"], **n)

    genre_to_games = defaultdict(list)
    for n in games:
        for gn in n["raw"].get("genres", []):
            if gn in DESIGN_DIMS:
                continue
            genre_to_games[gn].append(n["id"])
    for gn, ids in genre_to_games.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if GG.has_edge(ids[i], ids[j]):
                    GG[ids[i]][ids[j]]["weight"] += 1.0
                else:
                    GG.add_edge(ids[i], ids[j], weight=1.0)

    stud = defaultdict(list)
    for n in games:
        stud[n["raw"].get("developer_id")].append(n["id"])
    for did, ids in stud.items():
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if GG.has_edge(ids[i], ids[j]):
                    GG[ids[i]][ids[j]]["weight"] += 4.0
                else:
                    GG.add_edge(ids[i], ids[j], weight=4.0)
    return GG
