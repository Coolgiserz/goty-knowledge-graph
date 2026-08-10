# 统计机器学习流水线 · GOTY 知识图谱

`analysis/` 下是一套**可插拔**的图数据挖掘流水线，输入为 `data/graph.json`，输出 `analysis/output/`。分析结论的人读版见 [docs/INSIGHTS.md](INSIGHTS.md)（数据挖掘报告）。

---

## 1. 模块结构

```
analysis/ml/
├── config.py        # 所有超参数集中于此（改一处调全链路）
├── context.py       # PipelineContext：阶段间内存传数据，去除磁盘耦合
├── io_utils.py      # 图谱加载、共享计算（去重）
├── features.py      # FeatureEngine + 可注册特征组（Strategy）
├── clusterers.py    # 聚类算法策略注册表（kmeans / hierarchical / spectral / dbscan）
├── community.py     # 社区发现策略注册表（louvain / infomap / walktrap，可插拔）
├── analyzers.py     # Analyzer 基类 + 注册表（聚类 / 社区 / 热点 / 工作室风格 / GOTY 特征 / GOTY 品味网络）
├── visualizers.py   # Visualizer 基类 + 注册表（16 张图）
└── pipeline.py      # run_pipeline 统一编排：特征→分析→落盘→可视化→报告
```

**设计模式**：Strategy（聚类算法 / 社区发现 / 特征组可替换）、Registry（特征组 / 聚类器 / 社区探测器 / Analyzer / Visualizer 按注册表发现）、Pipeline（统一串联）、Context Object（内存传参）。**新增一种特征组 / 算法 / 分析 / 图表 = 写个子类并 `@register`，无需改动 pipeline。**

---

## 2. 运行

```bash
make analysis                       # 默认 KMeans + PCA 白化
python analysis/run_ml.py --clusterer spectral --no-pca   # 换谱聚类、关 PCA
python analysis/run_ml.py --exclude-reputation           # 关 studio_wins 防标签泄漏
python analysis/run_ml.py --k 6                          # 固定 k，跳过选 k
python analysis/run_ml.py --community infomap            # 社区发现主方法换成 Infomap
python analysis/run_ml.py --community walktrap           # 或换成 Walktrap（随机游走社区发现）
```

> 依赖见 `analysis/requirements.txt`（numpy / pandas / scikit-learn / networkx / matplotlib / scipy），建议在隔离 venv 中运行（见 `make analysis`）。`infomap` 为可选依赖（仅 Infomap 方法需要），其余随机游走方法（Walktrap、嵌入、个性化 PageRank）仅用 numpy / networkx / sklearn。

---

## 3. 产物

写入 `analysis/output/`：

- `factors.csv`：107 款游戏 × 63 列因子（图拓扑 4 / 属性 6 / 声誉 3 / 类型 one-hot 44 / 标识 6）
- `clusters.csv`、`communities.csv`、`communities_infomap.csv`、`communities_walktrap.csv`、`hotspot_era.csv`、`hotspot_year.csv` 及各 `*_profile.json`
- **`studio_similarity.csv` / `studio_style.csv` / `studio_style.json`**：开发商风格（**图谱距离 / 最短路径**视角）相似度矩阵、MDS 风格散点坐标
- **`studio_similarity_rw.csv` / `studio_style_rw.csv`**：同上，但为**随机游走嵌入**视角（两者并存、互为对照；`studio_style.json` 含两种视角的 Top 对与 Spearman 一致性 ρ）
- **`goty_genre.csv` / `goty_profile.json`**：GOTY vs 其他作品的区分因子（Cohen's d）与类型 Over-index
- **`goty_affinity.csv` / `goty_affinity.json`**：GOTY 品味网络——个性化 PageRank 给出的「喜欢 GOTY 还会喜欢…」推荐与工作室亲和力
- `ML_REPORT.md`：含聚类画像、社区画像、上升 / 下降类型、中心性排名、**开发商风格相似性（双视角）**、**GOTY 特征分析**、**GOTY 品味网络**等（共八节）
- 16 张 PNG：`factor_correlation.png` / `k_silhouette.png` / `cluster_pca.png` / `cluster_profile.png` / `community_graph.png` / `community_infomap.png` / **`community_walktrap.png`** / `hotspot_trend.png` / `centrality_top.png` / `studio_similarity_heatmap.png` / `studio_style_scatter.png` / **`studio_similarity_rw_heatmap.png`** / **`studio_style_rw_scatter.png`** / `goty_distinguish.png` / `goty_genre_overindex.png` / **`goty_affinity.png`**

---

## 4. 方法说明与注意事项

- “高频因子”在此指从图结构派生的细粒度截面因子矩阵；原数据没有日内 tick 级时序，年份是最细时间粒度。
- **聚类**默认**先做 PCA 白化**再 KMeans，以缓解 44 维类型 one-hot 带来的维度灾难；`studio_wins` 由 `is_goty` 派生（标签泄漏），可用 `--exclude-reputation` 关闭；轮廓系数普遍偏低（<0.25），簇为探索性划分而非严谨边界。
- **社区发现**采用**可插拔**策略：默认 **Louvain**（质量指标模块度 Q）作为主方法；无论主方法为何，**Infomap（地图方程）** 与 **Walktrap（随机游走距离层次聚并）** 两种随机游走方法都作为补充始终运行，报告三者对照（社区数 / 模块度 Q / 编码长度 L）。本数据三者社区数接近（Louvain 14 / Infomap 15 / Walktrap 11）、Q 几乎一致（0.6175 / 0.6173 / 0.5905），说明「玩法家族」结构稳健。可用 `--community {louvain|infomap|walktrap}` 切换主方法。
- **开发商风格（第五节）**用**两种并存视角**度量工作室风格接近度，互为印证而非替换：**A. 图谱距离（最短路径）**——在游戏-游戏投影图 GG 上测工作室间最短路径距离（一阶邻近性，相似度集中在约 0.3~0.7）；**B. 随机游走嵌入**——在完整异构图跑截断随机游走 → 游戏共现矩阵(log1p) → SVD 降维得「游戏嵌入」→ 工作室取均值 → 余弦相似度（二阶 / 多跳邻近性，相似度更分离，如 Bethesda↔CDPR 0.94、Rockstar↔圣莫尼卡 0.92）。两视角距离排序的 Spearman 相关 ρ≈0.8，高度一致；随机游走只是更平滑的等价尺子，正确定位是与最短路径并列的**探索手段**。
- **GOTY 品味网络（第七节）**：把全部 20 款 GOTY 获奖作作为种子，在完整异构图做**个性化 PageRank**，得到「喜欢 GOTY 的人还会喜欢谁」的推荐网络，自然浮现每家获奖作的「同门兄弟」（如 Bethesda 的辐射系列、CDPR 的赛博朋克）。
- **热点统计**以 **GOTY 获奖作本身**（每年 1 款、两半段各 10 款，固定样本、无「其他作品」分母偏差）衡量奖项「品味」演变；比较 2006–2015 与 2016–2025 两半段的类型占比（百分点 pp），并用滚动 3 年占比画图。每半段仅 10 款，结论为示意性趋势而非统计推断。
