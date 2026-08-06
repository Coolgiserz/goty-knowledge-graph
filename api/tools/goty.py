"""探索板块：GOTY 品味网络（个性化随机游走 / Personalized PageRank）。

复用 analysis/ml 的 goty_pagerank。参数：阻尼系数 alpha + 推荐条数 top_n。
注意：该分数与工作室在图中的体量 / GOTY 种子数高度相关，应作为
「工作室声望代理」来解读，而非纯粹的玩法相似度（见批判性反思文档）。
"""
from ..registry import ExplorationTool, register
from ..models import ParamSpec
from ..graph_loader import G_FULL, GAMES, STUDIO_NAME, data_matches_baseline
from analysis.ml.affinity import goty_pagerank


@register
class GotyTool(ExplorationTool):
    name = "goty"
    label = "GOTY 品味网络（PPR）"
    description = "从全部年度最佳(GOTY)出发做个性化 PageRank，得到「喜欢 GOTY 的人还会喜欢谁」。"

    params = [
        ParamSpec("alpha", "PageRank 阻尼 α", "float", 0.85,
                  min=0.1, max=0.99, step=0.05,
                  help="越大越贴近 GOTY 种子（局部），越小越扩散到全场"),
        ParamSpec("top_n", "推荐条数", "int", 10,
                  min=3, max=20, step=1,
                  help="推荐 / 榜单展示条数"),
    ]
    interpretation_defaults = {"alpha": 0.85, "top_n": 10}
    interpretation = (
        "**默认参数（α=0.85）下的结论**：以全部 GOTY 为种子做个性化 PageRank，"
        "浮现出每家获奖作的「同门兄弟」（如 Bethesda 的辐射系列、CDPR 的赛博朋克、"
        "Rockstar 的 RDR/GTA）。它与社区发现、工作室风格同源于一张图，"
        "但视角从「结构划分」转为「以 GOTY 为中心的影响力传播」。\n\n"
        "> **本板块的固有偏差（与参数无关，务必留意）**：该分数与工作室在图中的"
        "体量 / GOTY 种子数高度相关（相关系数约 0.6），排名≈工作室声望 × 种子数，"
        "而非纯玩法相似度；图内**没有玩家节点**，「喜欢 GOTY 的人还会喜欢谁」是修辞。"
        "调小 α 会让分数更扩散到全场、Top 推荐更「平均」，上述「同门兄弟」结构会被稀释。"
    )

    def run(self, params):
        summary = goty_pagerank(G_FULL, GAMES, STUDIO_NAME,
                                alpha=params["alpha"], top_n=params["top_n"])
        if summary["n_seeds"] == 0:
            return {"panels": [], "tables": [], "metrics": {"n_seeds": 0},
                    "error": "未找到 GOTY 种子"}

        tg = summary["top_games"]
        panel_games = {
            "type": "bar",
            "title": f"非 GOTY 推荐（亲和力 Top{params['top_n']}）",
            "data": {
                "categories": [g["title_zh"] for g in tg],
                "series": [{"name": "亲和力", "color": "#f5b301",
                            "values": [g["affinity"] for g in tg]}],
                "horizontal": True,
            },
        }
        ts = summary["top_studios"]
        panel_studios = {
            "type": "bar",
            "title": "工作室亲和力（其全部作品得分之和）",
            "data": {
                "categories": [s["studio"] for s in ts],
                "series": [{"name": "亲和力", "color": "#8e44ad",
                            "values": [s["affinity"] for s in ts]}],
                "horizontal": True,
            },
        }

        table_games = {
            "title": "非 GOTY 推荐明细",
            "columns": ["排名", "游戏", "工作室", "亲和力"],
            "rows": [[i + 1, g["title_zh"], g["studio"], g["affinity"]]
                     for i, g in enumerate(tg)],
        }
        table_studios = {
            "title": "工作室亲和力明细",
            "columns": ["排名", "工作室", "亲和力"],
            "rows": [[i + 1, s["studio"], s["affinity"]]
                     for i, s in enumerate(ts)],
        }

        metrics = {"n_seeds": summary["n_seeds"], "alpha": params["alpha"]}
        return {"panels": [panel_games, panel_studios],
                "tables": [table_games, table_studios],
                "metrics": metrics}
