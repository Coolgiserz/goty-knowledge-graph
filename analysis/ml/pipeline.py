"""管道编排（Pipeline / Template 模式）。

run_pipeline 统一串联：
  1) 特征工程 → factors / factor_doc
  2) 各 Analyzer（按注册表顺序）→ 各自 artifacts + 报告章节
  3) 落盘 CSV / JSON（保留旧文件名，便于后续接入网站 / 历史对比）
  4) 各 Visualizer（按注册表顺序）→ PNG
  5) 组装 ML_REPORT.md

所有阶段通过 PipelineContext 在内存中传递数据，互不依赖磁盘文件；
新增分析/可视化只需注册，无需改动本文件——即「可拓展、可插拔」。
"""
import os
import pandas as pd

from .config import MLConfig
from .context import PipelineContext
from .features import FeatureEngine
from .analyzers import Analyzer
from .visualizers import Visualizer
from .constants import (
    CSV_FACTORS, CSV_CLUSTERS, CSV_COMMUNITIES, CSV_HOTSPOT_ERA,
    CSV_STUDIO_SIM, CSV_STUDIO_STYLE, CSV_GOTY_GENRE,
    JSON_FACTOR_DOC, JSON_CLUSTER_PROFILE, JSON_COMMUNITY_PROFILE,
    JSON_HOTSPOT_SUMMARY, JSON_STUDIO_STYLE, JSON_GOTY_PROFILE,
    MD_REPORT, PNG,
)


def run_pipeline(config: MLConfig = None, graph_path: str = None, out_dir: str = None):
    config = config or MLConfig()
    ctx = PipelineContext(config, graph_path, out_dir)

    # ---- 1) 特征工程 ----
    df, factor_doc = FeatureEngine(config).extract(ctx)
    ctx.add("factors", df)
    ctx.add("factor_doc", factor_doc)

    # ---- 报告：头部 + 一、高频因子 ----
    n_games = len(df)
    n_goty = int(df["is_goty"].sum())
    ctx.write(
        "# 游戏知识图谱 · 数据挖掘报告（统计机器学习）\n",
        f"> 自动生成于 `analysis/run_ml.py`，输入为 `data/graph.json`"
        f"（{n_games} 款游戏 / {len(ctx.studio_names)} 家工作室 / {len(ctx.genre_names)} 个玩法类型）。\n",
        f"- 样本：{n_games} 款游戏（其中年度最佳 {n_goty} 款）\n",
        "- 方法：高频因子特征工程 → 聚类(可插拔算法+PCA) → Louvain 社区发现 → 时代热点统计"
        " → 开发商风格相似性 → 年度最佳(GOTY)特征分析\n",
        "- 产物：`analysis/output/` 下的 CSV / JSON / 7 张 PNG\n",
        "\n## 一、高频因子（特征工程）\n",
        f"把每张游戏节点视为一个「资产」，从图谱派生宽因子表（`{CSV_FACTORS}`，"
        f"{df.shape[1]} 列 = {len(factor_doc)} 个因子）。因子分四组：\n",
        "1. **图拓扑因子**：在「游戏-游戏相似投影图」上的度 / PageRank / 介数 / 聚类系数"
        "——衡量该游戏在玩法关系网中的中心性与桥接作用。\n",
        "2. **属性因子**：Metacritic 评分、年份、玩法类型数、设计维度（开放世界/合作/在线）。\n",
        "3. **声誉因子**：工作室夺冠数、作品总数、工作室 PageRank。\n",
        "4. **类型 one-hot**：每个玩法叶子类型一列。\n",
        f"\n> 说明：原数据没有日内 tick 级时间序列，年份是可用的最细时间粒度，"
        "故「高频」指**从图结构派生的细粒度截面因子**，而非高频时序。\n",
        f"> 评分缺失填补：{ctx.config.features.impute_rating}（共 {getattr(ctx, 'rating_imputed_n', 0)} 个“其他作品”无 Metacritic 分已填补）。\n",
        "> 特征选择提示：拓扑因子与类型 one-hot 对用户相似度信号存在重叠（冗余）；"
        "聚类默认先做 PCA 白化以缓解；studio_wins 由 is_goty 派生（标签泄漏），"
        f"当前 {'已包含' if ctx.config.features.include_studio_wins else '已关闭'}。\n",
    )

    # ---- 2) 各 Analyzer ----
    for A in Analyzer.all():
        res = A().analyze(ctx)
        for k, v in res.artifacts.items():
            ctx.add(k, v)
        ctx.report += res.report

    # ---- 3) 落盘 ----
    _persist(ctx)

    # ---- 4) 可视化 ----
    ctx.write(
        "\n## 七、中心性排行 & 因子相关性\n",
        "在玩法关系网中 PageRank 最高的游戏（枢纽/桥接型作品）：\n",
        f"![中心性Top]({PNG['centrality']})\n",
        "因子两两相关（识别多重共线，提示哪些因子信息重叠）：\n",
        f"![因子相关]({PNG['factor_corr']})\n",
    )
    for V in Visualizer.all():
        try:
            p = V().render(ctx)
            if p:
                print(f"[viz] {os.path.basename(p)}")
        except Exception as e:  # 单个图失败不影响整体
            print(f"[viz] {V.name} 失败: {e}")

    # ---- 5) 报告 ----
    _write_report(ctx)

    # ---- 控制台摘要 ----
    cp = ctx.get("cluster_profile")
    cop = ctx.get("community_profile")
    print(f"\n[ok] factors={df.shape}  clusters k={cp['best_k']}  "
          f"communities={cop['n_communities']} (Q={cop['modularity']})  -> {ctx.out_dir}")
    return ctx


def _persist(ctx: PipelineContext):
    out = ctx.out_dir
    a = ctx.artifacts
    if "factors" in a:
        a["factors"].to_csv(os.path.join(out, CSV_FACTORS), index=False, encoding="utf-8-sig")
    if "factor_doc" in a:
        import json as _json
        with open(os.path.join(out, JSON_FACTOR_DOC), "w", encoding="utf-8") as f:
            _json.dump(a["factor_doc"], f, ensure_ascii=False, indent=2)
    if "clusters" in a:
        a["clusters"].to_csv(os.path.join(out, CSV_CLUSTERS), index=False, encoding="utf-8-sig")
    if "cluster_profile" in a:
        import json as _json
        with open(os.path.join(out, JSON_CLUSTER_PROFILE), "w", encoding="utf-8") as f:
            _json.dump(a["cluster_profile"], f, ensure_ascii=False, indent=2)
    if "communities" in a:
        a["communities"].to_csv(os.path.join(out, CSV_COMMUNITIES), index=False, encoding="utf-8-sig")
    if "community_profile" in a:
        import json as _json
        with open(os.path.join(out, JSON_COMMUNITY_PROFILE), "w", encoding="utf-8") as f:
            _json.dump(a["community_profile"], f, ensure_ascii=False, indent=2)
    if "hotspot_era" in a:
        a["hotspot_era"].to_csv(os.path.join(out, CSV_HOTSPOT_ERA), index=False, encoding="utf-8-sig")
    if "hotspot_summary" in a:
        import json as _json
        with open(os.path.join(out, JSON_HOTSPOT_SUMMARY), "w", encoding="utf-8") as f:
            _json.dump(a["hotspot_summary"], f, ensure_ascii=False, indent=2)
    if "studio_style" in a:
        a["studio_style"].to_csv(os.path.join(out, CSV_STUDIO_STYLE), index=False, encoding="utf-8-sig")
    if "studio_sim_matrix" in a:
        blob = a["studio_sim_matrix"]
        pd.DataFrame(blob["matrix"], index=blob["studio_names"],
                     columns=blob["studio_names"]).to_csv(
            os.path.join(out, CSV_STUDIO_SIM), encoding="utf-8-sig")
    if "studio_style_summary" in a:
        import json as _json
        with open(os.path.join(out, JSON_STUDIO_STYLE), "w", encoding="utf-8") as f:
            _json.dump(a["studio_style_summary"], f, ensure_ascii=False, indent=2)
    if "goty_genre" in a:
        a["goty_genre"].to_csv(os.path.join(out, CSV_GOTY_GENRE), index=False, encoding="utf-8-sig")
    if "goty_profile" in a:
        import json as _json
        with open(os.path.join(out, JSON_GOTY_PROFILE), "w", encoding="utf-8") as f:
            _json.dump(a["goty_profile"], f, ensure_ascii=False, indent=2)


def _write_report(ctx: PipelineContext):
    path = os.path.join(ctx.out_dir, MD_REPORT)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(ctx.report))
    print(f"[report] wrote {path}")
