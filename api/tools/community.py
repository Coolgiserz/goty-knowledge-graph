"""探索板块：社区发现（玩法家族）。

复用 analysis/ml 的 CommunityDetector + community_profiles，
参数：主方法(louvain/infomap/walktrap) + Louvain 分辨率 + Infomap 重复 + Walktrap 步数。
解读默认值 = louvain + resolution 1.0；切换方法 / 调分辨率即视为偏离默认 → 解读失效。
"""
from ..registry import ExplorationTool, register
from ..models import ParamSpec
from ..graph_loader import GG, GAMES, NODES, data_matches_baseline
from analysis.ml.community import CommunityDetector, community_profiles
from analysis.ml.config import CommunityConfig, MLConfig

import networkx as nx


@register
class CommunityTool(ExplorationTool):
    name = "community"
    label = "社区发现（玩法家族）"
    description = "在游戏-游戏相似投影图上做社区划分，得到「玩法家族」及其画像。"

    params = [
        ParamSpec(
            "method", "社区发现方法", "select", "louvain",
            options=["louvain", "infomap", "walktrap"],
            help="主方法：Louvain(模块度) / Infomap(地图方程) / Walktrap(随机游走)"),
        ParamSpec(
            "resolution", "Louvain 分辨率", "float", 1.0,
            min=0.3, max=3.0, step=0.1, group="louvain",
            help="仅 Louvain 生效：越大社区越碎"),
        ParamSpec(
            "num_trials", "Infomap 重复次数", "int", 20,
            min=1, max=50, step=1, group="infomap",
            help="仅 Infomap 生效：越多越稳定"),
        ParamSpec(
            "walktrap_steps", "Walktrap 步数", "int", 4,
            min=1, max=10, step=1, group="walktrap",
            help="仅 Walktrap 生效：越大越偏向全局结构"),
    ]
    interpretation_defaults = {"method": "louvain", "resolution": 1.0}
    interpretation = (
        "**默认参数（Louvain / 分辨率 1.0）下的结论**：游戏在「玩法相似投影图」上自然聚成 "
        "十几个玩法家族（RPG / 动作 RPG、开放世界、独立叙事、竞技射击等）。这些家族与我们对"
        "「哪类游戏容易一起拿奖」的直觉基本一致。\n\n"
        "> 调节下方参数（尤其切换方法或改变分辨率）会改变社区边界与数量，"
        "此时的「玩法家族」划分已不同于默认，上述定性结论可能不再成立——"
        "请以图中实际划分为准。"
    )

    def run(self, params):
        cfg = CommunityConfig(
            method=params["method"], resolution=params["resolution"],
            seed=42, num_trials=params["num_trials"],
            walktrap_steps=params["walktrap_steps"])
        det = CommunityDetector.get(params["method"])()
        node2comm, info = det.detect(GG, cfg)
        profs = community_profiles(NODES, node2comm, MLConfig().design_dims)

        # 确定性布局：spring_layout(seed) 保证每次请求结果一致，前端直接按坐标绘制
        pos = nx.spring_layout(GG, weight="weight", seed=42)
        net_nodes = [{
            "id": n["id"],
            "label": n["raw"].get("title_zh") or n["raw"].get("title"),
            "community": int(node2comm.get(n["id"], -1)),
            "goty": bool(n["raw"].get("is_goty")),
            "x": round(float(pos[n["id"]][0]), 4),
            "y": round(float(pos[n["id"]][1]), 4),
        } for n in GAMES]
        net_edges = [{"from": u, "to": v} for u, v, _ in GG.edges(data=True)]

        panel_comm = {
            "type": "network",
            "title": f"社区结构（{params['method']}）",
            "data": {"nodes": net_nodes, "edges": net_edges},
        }
        bar = {
            "type": "bar",
            "title": "各社区规模",
            "data": {
                "categories": [f"C{p['community']}" for p in profs],
                "series": [{"name": "规模", "color": "#f5b301",
                            "values": [p["size"] for p in profs]}],
                "horizontal": False,
            },
        }

        table = {
            "title": "社区画像（按规模）",
            "columns": ["社区", "规模", "年度最佳成员", "代表游戏", "主导玩法类型", "均分"],
            "rows": [[
                f"C{p['community']}", p["size"],
                "、".join(p["goty_members"]) or "—",
                p["representative"],
                "、".join(f"{g}({v})" for g, v in p["top_genres"][:4]),
                p["avg_rating"],
            ] for p in profs],
        }

        metrics = {"method": params["method"],
                   "n_communities": len(profs),
                   "quality": info}
        return {"panels": [panel_comm, bar], "tables": [table], "metrics": metrics}
