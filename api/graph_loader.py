"""一次性加载 graph.json 并构建图对象，供所有探索板块复用（进程级单例）。

同时计算 graph.json 的 sha256，并与 analysis/_data_baseline.json 对照，
作为「数据有效性」的判定依据：数据漂移 → 文档快照(MD/INSIGHTS)中的预写解读失效。
"""
import os
import sys
import json
import hashlib
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from analysis.ml.io_utils import (  # noqa: E402
    load_graph, game_nodes, studio_names, build_game_graph, build_full_nx)
from analysis.ml.config import MLConfig  # noqa: E402

GRAPH_PATH = os.path.join(ROOT, "data", "graph.json")
BASELINE_PATH = os.path.join(ROOT, "analysis", "_data_baseline.json")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


_raw = load_graph(GRAPH_PATH)
G = _raw
NODES = G["nodes"]
EDGES = G["edges"]
GAMES = game_nodes(G)
STUDIO_NAME = studio_names(G)
CFG = MLConfig()
GG = build_game_graph(G, CFG)
G_FULL = build_full_nx(G)
SHA = _sha(GRAPH_PATH)


def node_counts():
    c = Counter(n.get("group") for n in NODES)
    return {
        "nodes": len(NODES),
        "edges": len(EDGES),
        "games": len(GAMES),
        "goty": sum(1 for n in GAMES if n["raw"].get("is_goty")),
        "studios": c.get("studio", 0),
        "genres": c.get("genre", 0),
        "awards": c.get("award", 0),
    }


def data_matches_baseline():
    """数据是否与文档快照基线一致：True 一致 / False 漂移 / None 基线缺失。"""
    try:
        b = json.load(open(BASELINE_PATH, encoding="utf-8"))
        return b.get("sha256") == SHA
    except Exception:
        return None
