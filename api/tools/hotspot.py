"""探索板块：时代热点（奖项的「品味」如何演变）。

复用 analysis/ml 的 GOTY 时间线逻辑（HotspotAnalyzer 的口径）：热点 = GOTY 获奖作
本身的类型构成随时间的变化（每年 1 款、两半段各 10 款，样本固定、无分母偏差）。
参数：前后半段分界年 split_year。
"""
from collections import Counter

from ..registry import ExplorationTool, register
from ..models import ParamSpec
from ..graph_loader import GAMES, STUDIO_NAME, data_matches_baseline
from analysis.ml.config import MLConfig


@register
class HotspotTool(ExplorationTool):
    name = "hotspot"
    label = "时代热点（奖项品味演变）"
    description = "GOTY 获奖作的类型构成随时间的上升 / 下降趋势。"

    params = [
        ParamSpec("split_year", "前后半段分界年", "int", 2015,
                  min=2010, max=2020, step=1,
                  help="此前为前半段、此后为后半段，用于比较上升/下降"),
    ]
    interpretation_defaults = {"split_year": 2015}
    interpretation = (
        "**默认分界（≤2015 vs >2015）下的结论**：以 GOTY 获奖作为固定样本（每年 1 款、"
        "两半段各 10 款），观察玩法类型与设计维度占比的前后变化，可粗略看到某些类型"
        "（如开放世界、服务型/在线）的上升与其他类型的下降。\n\n"
        "> **样本极小（每半段仅 10 款）**：结果为**示意性趋势**而非统计推断；"
        "移动分界年会改变前后样本量，从而改变占比口径与「上升/下降」结论——"
        "请把它当作探索工具，而非定论。"
    )

    def run(self, params):
        dd = MLConfig().design_dims
        YEAR_LO, YEAR_HI = 2006, 2025
        years = list(range(YEAR_LO, YEAR_HI + 1))
        goty = [n for n in GAMES
                if n["raw"].get("is_goty")
                and isinstance(n["raw"].get("year"), int)
                and YEAR_LO <= n["raw"]["year"] <= YEAR_HI]
        goty_sorted = sorted(goty, key=lambda n: n["raw"]["year"])

        year_tag = {y: Counter() for y in years}
        for n in goty_sorted:
            for t in n["raw"].get("tiers", []):
                year_tag[n["raw"]["year"]][t] += 1

        all_tag = Counter()
        for y in years:
            all_tag.update(year_tag[y])
        gameplay_top = [t for t, _ in all_tag.most_common() if t not in dd][:7]
        key_dims = [d for d in ("开放世界", "多人合作", "在线") if d in dd]
        key_set = key_dims + gameplay_top

        split = params["split_year"]
        h1 = [n for n in goty_sorted if n["raw"]["year"] <= split]
        h2 = [n for n in goty_sorted if n["raw"]["year"] > split]
        n1, n2 = len(h1), len(h2)

        def _cnt(games):
            c = Counter()
            for n in games:
                for t in n["raw"].get("tiers", []):
                    c[t] += 1
            return c

        t1, t2 = _cnt(h1), _cnt(h2)
        trend = []
        for t in key_set:
            a, b = t1.get(t, 0), t2.get(t, 0)
            trend.append({
                "tag": t, "first": a, "second": b,
                "first_pp": round(100.0 * a / n1, 1) if n1 else 0.0,
                "second_pp": round(100.0 * b / n2, 1) if n2 else 0.0,
                "delta_pp": round(100.0 * (b - a) / n1, 1) if n1 else 0.0,
                "is_design": t in dd,
            })
        rising = sorted([x for x in trend if x["delta_pp"] > 0], key=lambda x: -x["delta_pp"])
        falling = sorted([x for x in trend if x["delta_pp"] < 0], key=lambda x: x["delta_pp"])

        studio_wins = Counter()
        for n in goty_sorted:
            sid = n["raw"].get("developer_id")
            studio_wins[STUDIO_NAME.get(sid, n["raw"].get("developer"))] += 1
        studio_tally = [{"studio": k, "wins": v} for k, v in studio_wins.most_common()]

        bar = {
            "type": "bar",
            "title": f"类型占比：≤{split}（{n1}款） vs >{split}（{n2}款）",
            "data": {
                "categories": [t["tag"] for t in trend],
                "series": [
                    {"name": f"≤{split}", "color": "#3b6ea5",
                     "values": [t["first_pp"] for t in trend]},
                    {"name": f">{split}", "color": "#f5b301",
                     "values": [t["second_pp"] for t in trend]},
                ],
                "horizontal": True,
            },
        }
        table = {
            "title": "类型趋势（上升 / 下降）",
            "columns": ["类型", "前pp", "后pp", "Δpp", "设计维度"],
            "rows": [[t["tag"], t["first_pp"], t["second_pp"], t["delta_pp"],
                      "是" if t["is_design"] else ""]
                     for t in (rising + falling)],
        }
        table2 = {
            "title": "GOTY 主导工作室（夺冠次数）",
            "columns": ["工作室", "GOTY次数"],
            "rows": [[s["studio"], s["wins"]] for s in studio_tally[:8]],
        }
        metrics = {"n_first_half": n1, "n_second_half": n2, "split_year": split,
                   "rising": [t["tag"] for t in rising[:5]],
                   "falling": [t["tag"] for t in falling[:5]]}
        return {"panels": [bar], "tables": [table, table2], "metrics": metrics}
