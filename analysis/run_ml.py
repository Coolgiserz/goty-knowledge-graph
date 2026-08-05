"""编排：依次运行 高频因子 → 聚类 → 社区发现 → 热点统计 → 可视化，并生成 ML_REPORT.md。

用法（在隔离 venv 中）:
  python analysis/run_ml.py
"""
import os
import sys
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml"))

from graphio import ensure_out, OUT_DIR
from factors import main as factors_main
from cluster import main as cluster_main
from community import main as community_main
from hotspot import main as hotspot_main
from visualize import main as viz_main

L = []


def w(s=""):
    L.append(s)


def run():
    print("=== 1/5 高频因子 ===")
    df, genre_names, factor_doc = factors_main()

    print("=== 2/5 聚类 ===")
    cdf, best_k, scores, profiles = cluster_main(df, genre_names)

    print("=== 3/5 社区发现 ===")
    comm_df, mod, comm_prof = community_main()

    print("=== 4/5 热点统计 ===")
    era_df, summary = hotspot_main()

    print("=== 5/5 可视化 ===")
    viz_main()

    build_report(df, factor_doc, best_k, scores, profiles, mod, comm_prof, summary)


def build_report(df, factor_doc, best_k, scores, profiles, mod, comm_prof, summary):
    n_games = len(df)
    n_goty = int(df["is_goty"].sum())

    w("# 游戏知识图谱 · 数据挖掘报告（统计机器学习）\n")
    w("> 自动生成于 `analysis/run_ml.py`，输入为 `data/graph.json`（107 款游戏 / 15 家工作室 / 47 个类型）。\n")
    w(f"- 样本：{n_games} 款游戏（其中年度最佳 {n_goty} 款）\n")
    w(f"- 方法：高频因子特征工程 → KMeans/层次聚类 → Louvain 社区发现 → 时代热点统计\n")
    w(f"- 产物：`analysis/output/` 下的 CSV / JSON / 7 张 PNG\n")

    # ---- 高频因子 ----
    w("\n## 一、高频因子（特征工程）\n")
    w("把每张游戏节点视为一个“资产”，从图谱派生宽因子表（`factors.csv`，")
    w(f"{df.shape[1]} 列 = {len(factor_doc)} 个因子）。因子分四组：\n")
    w("1. **图拓扑因子**：在“游戏-游戏相似投影图”上的度 / PageRank / 介数 / 聚类系数")
    w("——衡量该游戏在玩法关系网中的中心性与桥接作用。\n")
    w("2. **属性因子**：Metacritic 评分、年份、玩法类型数、设计维度（开放世界/合作/在线）。\n")
    w("3. **声誉因子**：工作室夺冠数、作品总数、工作室 PageRank。\n")
    w("4. **类型 one-hot**：每个玩法叶子类型一列。\n")
    w("\n> 说明：原数据没有日内 tick 级时间序列，年份是可用的最细时间粒度，")
    w("故“高频”指**从图结构派生的细粒度截面因子**，而非高频时序。\n")

    # ---- 聚类 ----
    w("\n## 二、聚类（KMeans + 层次）\n")
    w(f"在标准化因子矩阵上以轮廓系数选优，最优 **k={best_k}**（轮廓={scores[best_k]:.3f}）。")
    w(f"> 注：轮廓系数偏低，说明游戏在因子空间中呈连续谱，k={best_k} 仅为探索性划分，")
    w("并非严谨的类别边界。\n")
    w("各簇画像：\n")
    w("| 簇 | 规模 | 年度最佳 | GOTY率 | 均分 | 代表游戏(前5) | 主导玩法类型 |")
    w("|---|---|---|---|---|---|---|")
    for p in profiles:
        topg = "、".join(f"{gn}({v:.2f})" for gn, v in p["top_genres"][:5])
        games = "、".join(p["top_games"][:5])
        w(f"| 簇{p['cluster']} | {p['size']} | {p['goty']} | {p['goty_rate']:.2f} | "
          f"{p['avg_rating']} | {games} | {topg} |")
    w("\n![聚类PCA](cluster_pca.png)\n")
    w("![簇画像](cluster_profile.png)\n")
    w("![k选择](k_silhouette.png)\n")

    # ---- 社区发现 ----
    w("\n## 三、社区发现（Louvain）\n")
    w(f"在游戏-游戏相似投影图上做 Louvain 划分，得到 **{len(comm_prof)} 个社区**，")
    w(f"**模块度 Q={mod:.4f}**（越接近 1 划分越清晰）。社区即“玩法家族”：\n")
    w("| 社区 | 规模 | 年度最佳成员 | 代表游戏 | 主导玩法类型 |")
    w("|---|---|---|---|---|")
    for c in comm_prof:
        topg = "、".join(f"{gn}({v})" for gn, v in c["top_genres"][:5])
        goty = "、".join(c["goty_members"]) or "—"
        w(f"| C{c['community']} | {c['size']} | {goty} | {c['representative']} | {topg} |")
    w("\n![社区图](community_graph.png)\n")

    # ---- 热点统计 ----
    w("\n## 四、热点统计（时代演变）\n")
    w("时代分桶：" + " / ".join(summary["eras"]) + "\n")
    up = "、".join(f"{r['genre']}(+{r['delta']:.2f})" for r in summary["rising_genres"][:6])
    down = "、".join(f"{r['genre']}({r['delta']:.2f})" for r in summary["falling_genres"][:5])
    w(f"- **上升类型**（末代−首代占比差）：{up}\n")
    w(f"- **下降类型**：{down}\n")
    w("\n**工作室滚动热度（近5年 GOTY 夺冠数 Top3）：**\n")
    w("| 年份 | 热门工作室 |")
    w("|---|---|")
    for sw in summary["studio_rolling_hotness"]:
        s = "、".join(f"{k}({v})" for k, v in sw["top_studios"])
        w(f"| {sw['year']} | {s} |")
    w("\n![类型热度](hotspot_trend.png)\n")

    # ---- 中心性 + 因子相关 ----
    w("\n## 五、中心性排行 & 因子相关性\n")
    w("在玩法关系网中 PageRank 最高的游戏（枢纽/桥接型作品）：\n")
    w("![中心性Top](centrality_top.png)\n")
    w("因子两两相关（识别多重共线，提示哪些因子信息重叠）：\n")
    w("![因子相关](factor_correlation.png)\n")

    # ---- 复现 ----
    w("\n## 六、复现方式\n")
    w("```bash")
    w("VENV=/Users/tarnished/.workbuddy/binaries/python/envs/default/bin/python")
    w("cd <repo>")
    w("$VENV analysis/run_ml.py")
    w("```\n")
    w("或分步运行 `analysis/ml/` 下各模块（`factors / cluster / community / hotspot / visualize`）。\n")

    out = ensure_out()
    path = os.path.join(out, "ML_REPORT.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"\n[report] wrote {path}")


if __name__ == "__main__":
    run()
