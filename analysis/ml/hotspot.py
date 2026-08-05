"""热点统计：类型 / 工作室随时间的“热度”演变。

- 时代分桶：2006-2010 / 2011-2015 / 2016-2020 / 2021-2025
- 每个时代内各玩法类型的占比（share = 含该类型的游戏数 / 时代游戏数）
- 突现类型：对比“最近一个时代”与“最早一个时代”的占比差（上升/下降）
- 工作室热度：滚动 5 年窗口的年度最佳夺冠数
- GOTY 时间线：每年获奖游戏的顶层类型
输出 hotspot_era.csv + hotspot_summary.json。
"""
import json
import os
import numpy as np
import pandas as pd
from collections import defaultdict

from graphio import load_graph, game_nodes, ensure_out, OUT_DIR, DESIGN_DIMS

ERAS = [(2006, 2010, "2006-2010"), (2011, 2015, "2011-2015"),
        (2016, 2020, "2016-2020"), (2021, 2025, "2021-2025")]


def era_of(year):
    for lo, hi, name in ERAS:
        if lo <= year <= hi:
            return name
    return None


def main():
    g = load_graph()
    games = game_nodes(g)
    byid = {n["id"]: n for n in g["nodes"]}
    # 工作室 developer_id -> 规范中文名（避免同一家被拆成多个展示串）
    studio_name = {n["id"]: n["raw"].get("name_zh") or n["raw"].get("name")
                   for n in g["nodes"] if n["group"] == "studio"}
    genre_names = sorted({n["raw"]["name"] for n in g["nodes"]
                          if n["group"] == "genre" and n["raw"]["name"] not in DESIGN_DIMS})

    # ---- 时代类型占比 ----
    era_game_count = defaultdict(int)
    era_genre_count = defaultdict(lambda: defaultdict(int))
    for n in games:
        y = n["raw"].get("year")
        e = era_of(y)
        if not e:
            continue
        era_game_count[e] += 1
        for gn in n["raw"].get("genres", []):
            if gn in DESIGN_DIMS:
                continue
            era_genre_count[e][gn] += 1

    rows = []
    for lo, hi, name in ERAS:
        total = era_game_count[name]
        for gn in genre_names:
            cnt = era_genre_count[name].get(gn, 0)
            rows.append({"era": name, "genre": gn, "count": cnt,
                         "share": round(cnt / total, 4) if total else 0.0})
    era_df = pd.DataFrame(rows)

    # ---- 突现类型：末代 - 首代 share 差 ----
    first, last = ERAS[0][2], ERAS[-1][2]
    first_map = {r["genre"]: r["share"] for r in rows if r["era"] == first}
    last_map = {r["genre"]: r["share"] for r in rows if r["era"] == last}
    rising = []
    for gn in genre_names:
        diff = last_map.get(gn, 0) - first_map.get(gn, 0)
        rising.append({"genre": gn, "first_share": first_map.get(gn, 0),
                       "last_share": last_map.get(gn, 0), "delta": round(diff, 4)})
    rising.sort(key=lambda x: -x["delta"])
    rising_up = [r for r in rising if r["delta"] > 0][:10]
    rising_down = [r for r in rising if r["delta"] < 0][:10]

    # ---- 工作室滚动热度（5 年窗口 GOTY 数）----
    goty_years = sorted({n["raw"]["year"] for n in games if n["raw"].get("is_goty")})
    studio_window = []
    for y in goty_years:
        wins = defaultdict(int)
        for n in games:
            if n["raw"].get("is_goty") and y - 4 <= n["raw"]["year"] <= y:
                sid = n["raw"].get("developer_id")
                wins[studio_name.get(sid, n["raw"].get("developer"))] += 1
        top = sorted(wins.items(), key=lambda x: -x[1])[:3]
        studio_window.append({"year": y, "top_studios": [[k, v] for k, v in top]})

    # ---- GOTY 时间线（顶层类型）----
    timeline = []
    for n in sorted([x for x in games if x["raw"].get("is_goty")], key=lambda x: x["raw"]["year"]):
        timeline.append({"year": n["raw"]["year"], "title_zh": n["raw"]["title_zh"],
                         "tiers": n["raw"].get("tiers", []), "rating": n["raw"].get("player_rating")})

    summary = {
        "eras": [e[2] for e in ERAS],
        "era_game_counts": dict(era_game_count),
        "rising_genres": rising_up,
        "falling_genres": rising_down,
        "studio_rolling_hotness": studio_window,
        "goty_timeline": timeline,
    }

    out = ensure_out()
    era_df.to_csv(os.path.join(out, "hotspot_era.csv"), index=False, encoding="utf-8-sig")
    with open(os.path.join(out, "hotspot_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"[hotspot] wrote hotspot_era.csv ({len(era_df)} rows) + hotspot_summary.json; "
          f"rising top3: {[r['genre'] for r in rising_up[:3]]}")
    return era_df, summary


if __name__ == "__main__":
    main()
