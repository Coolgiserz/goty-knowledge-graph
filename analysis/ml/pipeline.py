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
    CSV_FACTORS, CSV_CLUSTERS, CSV_COMMUNITIES, CSV_COMMUNITIES_IM, CSV_COMMUNITIES_WT,
    CSV_HOTSPOT_ERA, CSV_HOTSPOT_YEAR,
    CSV_STUDIO_SIM, CSV_STUDIO_SIM_RW, CSV_STUDIO_STYLE, CSV_STUDIO_STYLE_RW,
    CSV_GOTY_GENRE, CSV_GOTY_AFFINITY,
    JSON_FACTOR_DOC, JSON_CLUSTER_PROFILE, JSON_COMMUNITY_PROFILE,
    JSON_COMMUNITY_PROFILE_IM, JSON_COMMUNITY_PROFILE_WT,
    JSON_HOTSPOT_SUMMARY, JSON_STUDIO_STYLE, JSON_GOTY_PROFILE, JSON_GOTY_AFFINITY,
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
        "- 方法：高频因子特征工程 → 聚类(可插拔算法+PCA) → 社区发现"
        "(Louvain 主方法 + Infomap/Walktrap 随机游走对照) → 时代热点统计"
        " → 开发商风格相似性(图谱距离 + 随机游走嵌入 双视角) → 年度最佳(GOTY)特征分析"
        " → GOTY 品味网络(个性化随机游走)\n",
        "- 产物：`analysis/output/` 下的 CSV / JSON / 16 张 PNG\n",
        "\n> **⚠️ 局限性声明（阅读前必读）**\n"
        "> 本报告是**探索性描述分析**，旨在从这张精选图谱上**产生假设**，所有结论均未经外部验证 / 随机对照，请以「呈现」而非「发现」看待。主要边界条件：\n"
        "> 1. **选择偏差**：107 款游戏 = 20 款 GOTY + 87 款“其他作品”，且这 87 款**全部来自同一批 15 家 GOTY 获奖工作室**。故“GOTY vs 其他”实为**同工作室获奖作 vs 其非获奖作**的配对比较，并非“获奖作 vs 全体游戏”；文中“其他作品”均不含非获奖工作室的作品。\n"
        "> 2. **年份混杂**：others 含 1996 年等老游戏，GOTY 均年(2015.5) 与 others 均年(2011.6) 的差距部分由收录方式人为造成。\n"
        "> 3. **评分重言式**：`player_rating` 由 GOTY 定义本身决定，其高区分度属预期而非独立证据；`studio_wins` 由 `is_goty` 派生（标签泄漏），已按配置决定是否纳入因子表。\n"
        "> 4. **PPR“品味网络”为工作室声望代理**：亲和力与 GOTY 种子数相关 ρ=0.635、与作品数相关 ρ=0.46，排名≈工作室体量×种子数；图内无玩家节点，“品味/推荐”为修辞。\n"
        "> 5. **方法同源性**：聚类 / 社区发现 / 工作室相似 / GOTY 类型 over-index 四类方法高度依赖“类型”这一信号；工作室相似双视角（图谱距离 vs 随机游走）距离排序 Spearman ρ=0.80，高度一致说明它们度量同一结构，新增独立洞察有限。\n"
        "> 6. **稳定性弱**：聚类轮廓 0.160（<0.25）、社区数 14/15/11、热点每半段仅 10 款，均提示探索性而非确定结论。\n"
        "> 详见 `批判性反思_知识图谱分析.md`。\n",
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
        "\n## 八、中心性排行 & 因子相关性\n",
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
    cim = ctx.get("community_profile_infomap")
    cim_txt = f"  infomap_modules={cim['n_communities']}(L={cim['quality'].get('codelength')})" if cim else ""
    print(f"\n[ok] factors={df.shape}  clusters k={cp['best_k']}  "
          f"communities={cop['n_communities']}(method={cop['method']}){cim_txt}  -> {ctx.out_dir}")
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
    if "communities_infomap" in a:
        a["communities_infomap"].to_csv(os.path.join(out, CSV_COMMUNITIES_IM),
                                        index=False, encoding="utf-8-sig")
    if "community_profile_infomap" in a:
        import json as _json
        with open(os.path.join(out, JSON_COMMUNITY_PROFILE_IM), "w", encoding="utf-8") as f:
            _json.dump(a["community_profile_infomap"], f, ensure_ascii=False, indent=2)
    if "communities_walktrap" in a:
        a["communities_walktrap"].to_csv(os.path.join(out, CSV_COMMUNITIES_WT),
                                         index=False, encoding="utf-8-sig")
    if "community_profile_walktrap" in a:
        import json as _json
        with open(os.path.join(out, JSON_COMMUNITY_PROFILE_WT), "w", encoding="utf-8") as f:
            _json.dump(a["community_profile_walktrap"], f, ensure_ascii=False, indent=2)
    if "hotspot_era" in a:
        a["hotspot_era"].to_csv(os.path.join(out, CSV_HOTSPOT_ERA), index=False, encoding="utf-8-sig")
    if "hotspot_year" in a:
        a["hotspot_year"].to_csv(os.path.join(out, CSV_HOTSPOT_YEAR), index=False, encoding="utf-8-sig")
    if "hotspot_summary" in a:
        import json as _json
        with open(os.path.join(out, JSON_HOTSPOT_SUMMARY), "w", encoding="utf-8") as f:
            _json.dump(a["hotspot_summary"], f, ensure_ascii=False, indent=2)
    if "studio_style_sp" in a:
        a["studio_style_sp"].to_csv(os.path.join(out, CSV_STUDIO_STYLE), index=False, encoding="utf-8-sig")
    if "studio_sim_sp_matrix" in a:
        blob = a["studio_sim_sp_matrix"]
        pd.DataFrame(blob["matrix"], index=blob["studio_names"],
                     columns=blob["studio_names"]).to_csv(
            os.path.join(out, CSV_STUDIO_SIM), encoding="utf-8-sig")
    if "studio_style_rw" in a:
        a["studio_style_rw"].to_csv(os.path.join(out, CSV_STUDIO_STYLE_RW), index=False, encoding="utf-8-sig")
    if "studio_sim_rw_matrix" in a:
        blob = a["studio_sim_rw_matrix"]
        pd.DataFrame(blob["matrix"], index=blob["studio_names"],
                     columns=blob["studio_names"]).to_csv(
            os.path.join(out, CSV_STUDIO_SIM_RW), encoding="utf-8-sig")
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
    if "goty_affinity" in a:
        a["goty_affinity"].to_csv(os.path.join(out, CSV_GOTY_AFFINITY),
                                  index=False, encoding="utf-8-sig")
    if "goty_affinity_summary" in a:
        import json as _json
        with open(os.path.join(out, JSON_GOTY_AFFINITY), "w", encoding="utf-8") as f:
            _json.dump(a["goty_affinity_summary"], f, ensure_ascii=False, indent=2)


def _write_report(ctx: PipelineContext):
    path = os.path.join(ctx.out_dir, MD_REPORT)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(ctx.report))
    print(f"[report] wrote {path}")
