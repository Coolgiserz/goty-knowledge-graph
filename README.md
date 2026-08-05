# 🎮 近 20 年年度最佳游戏知识图谱（GOTY Knowledge Graph）

一个覆盖 **2006–2025** 年度最佳游戏（Game of the Year）的开放知识图谱：既可**在浏览器里交互式浏览**力导向图谱，也能把整套数据**一键导入 Neo4j** 做图查询分析。

> 数据来源：Spike VGA / VGX（2006–2013）+ The Game Awards（2014–2025，含 2025 年黑马《光与影：33 号远征队》）。

---

## ✨ 特性

- **交互式知识图谱网站**：力导向图，点击节点看详情；支持按**年份 / 工作室 / 类型**筛选、关键词搜索、图谱↔表格双视图。
- **完整属性**：每款年度最佳游戏都挖掘了类别 / 玩法 / 独特之处 / 缺点·争议 / 评分 / 主要奖项 / 影响力。
- **重点：开发商其他作品**：不仅收录 GOTY，还展开其开发商的**其他代表作**（如 Rockstar 的 GTA 前作与 RDR、FromSoftware 的魂系列、Naughty Dog 的神秘海域等），形成「工作室 → 作品」关系网。
- **可导入 Neo4j**：提供标准 CSV 数据集 + 两种导入方式（离线 `neo4j-admin import` / 在线 `LOAD CSV`）+ 自动导入脚本。
- **开箱即部署**：纯静态站点（无后端），支持本地双击打开、`python` 静态服务器、以及 **Docker / docker-compose 一键部署**。

## 📊 数据规模

| 维度 | 数量 |
|------|------|
| 年度最佳游戏（GOTY） | 20 |
| 游戏节点（含开发商其他作品） | 107 |
| 开发商 | 15 |
| 游戏类型（15 顶层原子类别 + 子类型，含层级） | 47 |
| 年度大奖节点 | 20 |
| 关系（边） | 308 |

---

## 🚀 快速开始

### 方式一：仅浏览网站（本地，零依赖）

```bash
make serve            # 默认 http://localhost:8080
# 或：
cd site && python3 -m http.server 8080
```

也可以**直接双击 `site/index.html`** 离线打开（数据已内联，无需联网）。

### 方式二：Docker 托管网站

```bash
make docker           # 构建镜像 goty-knowledge-graph
make run              # 运行容器，访问 http://localhost:8080
```

### 方式三：全栈（网站 + 自动导入的 Neo4j）

```bash
make up
# 网站：       http://localhost:8080
# Neo4j Browser： http://localhost:7474  （用户名 neo4j / 密码 password123）
```

`docker-compose` 会在 Neo4j 健康检查通过后，由 `importer` 服务自动执行 `scripts/init.cypher` 把数据集导入图库。

### 方式四：导入到你自己的 Neo4j 实例

如果你已有 Neo4j（非云 Aura），把 `data/csv/` 放到其 `import` 目录后执行：

```bash
cypher-shell -u <user> -p <password> -f scripts/init.cypher
# 或一键起一个本地 Neo4j 并自动导入：
make neo4j
```

> 详细说明与示例查询见 **[docs/neo4j_tutorial.md](docs/neo4j_tutorial.md)**。

---

## 📁 目录结构

```
.
├── README.md
├── LICENSE                 # MIT
├── Makefile                # 快捷命令（build/serve/docker/up/neo4j）
├── Dockerfile              # 网站静态托管镜像（nginx）
├── docker-compose.yml      # web + neo4j + 自动导入
├── docker/nginx.conf       # nginx 站点配置
├── .dockerignore
├── src/
│   ├── build.py            # 合并原始数据 → 数据集（CSV + graph.json）
│   └── build_site.py       # 生成交互式网站 site/index.html
├── data/
│   ├── raw/                # 原始研究数据（5 个 agent 的结构化 JSON）
│   ├── csv/                # 普通表头 CSV（供 Cypher LOAD CSV）
│   ├── neo4j/              # 冒号表头 CSV（供 neo4j-admin import）
│   └── graph.json          # 合并后的图谱（供网站 / 检查）
├── site/                   # 生成的可部署静态站点（index.html + assets/）
├── vendor/                 # 第三方库（vis-network.min.js）
├── scripts/
│   ├── init.cypher         # Neo4j 自动导入脚本
│   ├── serve.sh            # 本地静态服务器
│   └── neo4j_import.sh     # 单独起 Neo4j 并导入
├── analysis/
│   ├── run_ml.py           # 统计机器学习流水线入口（瘦 CLI）
│   ├── ml/                 # 可插拔数据挖掘包：config/features/clusterers/analyzers/visualizers/pipeline
│   ├── requirements.txt    # 依赖（numpy/pandas/scikit-learn/networkx/matplotlib/scipy）
│   └── output/             # 运行产物：CSV / JSON / PNG / ML_REPORT.md
└── docs/
    ├── neo4j_tutorial.md   # Neo4j 导入教程
    ├── INSIGHTS.md         # 数据挖掘报告（奖项品味 / 工作室格局 / 评分门槛 / 研究课题）
    └── DEVELOPMENT.md      # 如何重新生成 / 扩展数据
```
（运行后会生成 `analysis/output/ML_REPORT.md` 与 11 张可视化 PNG。）

---

## 🧠 数据模型

**节点（标签）**：`Game`、`Studio`、`Genre`、`Award`
**关系（边）**：
- `(:Studio)-[:DEVELOPED]->(:Game)` — 开发商开发了某游戏
- `(:Game)-[:WON]->(:Award)` — 该游戏获得年度最佳（仅 GOTY 有）
- `(:Game)-[:BELONGS_TO_GENRE]->(:Genre)` — 游戏所属原子类型（一款游戏可属于多个类型）
- `(:Genre)-[:SUBCLASS_OF]->(:Genre)` — 类型层级（子类型隶属于父类型，直至 12 个玩法顶层类别）

**分类模型（两层）**：游戏同时带有「玩法类别」与「设计维度」两类标签。
- 玩法类别（12 顶层 + 子类型，构成 SUBCLASS_OF 层级）：角色扮演 / 动作 / 射击 / 冒险 / 动作冒险 / 平台跳跃 / 策略 / 模拟 / 竞速 / 卡牌 / 解谜 / 虚拟现实。
- 设计维度（跨玩法的特征标签，可与任意玩法类别叠加，本身为顶层、无父/子）：**开放世界 / 多人合作 / 在线**。例如筛选「开放世界」即可汇聚艾尔登法环、旷野之息、天际、GTA、巫师3 等全部开放世界游戏；「开放世界 ∩ 动作冒险」的交集中自然包含艾尔登法环。

---

## 🔧 如何重新生成 / 扩展

所有数据从 `data/raw/*.json` 经 `src/build.py` 合并、再由 `src/build_site.py` 生成站点：

```bash
make build             # = build.py + build_site.py
```

- **修正 / 补充某款游戏**：编辑 `data/raw/agentN.json` 后 `make build`。
- **新增游戏或工作室**：在对应 `agentN.json` 的 `goty_games` / `other_games` 中追加条目，重跑 `make build`。
- **重新拉取第三方库**：从 `https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js` 更新 `vendor/vis-network.min.js`。

详见 **[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)**。

---

## 🔬 统计机器学习（数据挖掘）

`analysis/` 下是一套**可插拔**的图数据挖掘流水线，输入为 `data/graph.json`，输出 `analysis/output/`。

```
analysis/ml/
├── config.py        # 所有超参数集中于此（改一处调全链路）
├── context.py       # PipelineContext：阶段间内存传数据，去除磁盘耦合
├── io_utils.py      # 图谱加载、共享计算（去重）
├── features.py      # FeatureEngine + 可注册特征组（Strategy）
├── clusterers.py    # 聚类算法策略注册表（kmeans/hierarchical/spectral/dbscan）
├── community.py     # 社区发现策略注册表（louvain / infomap，可插拔）
├── analyzers.py     # Analyzer 基类 + 注册表（聚类 / 社区 / 热点 / 工作室风格 / GOTY 特征）
├── visualizers.py   # Visualizer 基类 + 注册表（12 张图）
└── pipeline.py      # run_pipeline 统一编排：特征→分析→落盘→可视化→报告
```

**设计模式**：Strategy（聚类算法 / 社区发现 / 特征组可替换）、Registry（特征组 / 聚类器 / 社区探测器 / Analyzer / Visualizer 按注册表发现）、Pipeline（统一串联）、Context Object（内存传参）。**新增一种特征组 / 算法 / 分析 / 图表 = 写个子类并 `@register`，无需改动 pipeline。**

运行：

```bash
make analysis                       # 默认 KMeans + PCA 白化
python analysis/run_ml.py --clusterer spectral --no-pca   # 换谱聚类、关 PCA
python analysis/run_ml.py --exclude-reputation           # 关 studio_wins 防标签泄漏
python analysis/run_ml.py --k 6                          # 固定 k，跳过选 k
python analysis/run_ml.py --community infomap            # 社区发现主方法换成 Infomap
```

> 依赖见 `analysis/requirements.txt`，建议在隔离 venv 中运行（见 `make analysis`）。

产物写入 `analysis/output/`：
- `factors.csv`：107 款游戏 × 63 列因子（图拓扑 4 / 属性 6 / 声誉 3 / 类型 one-hot 44 / 标识 6）
- `clusters.csv`、`communities.csv`、`communities_infomap.csv`、`hotspot_era.csv`、`hotspot_year.csv` 及各 `*_profile.json`
- **`studio_style.csv` / `studio_similarity.csv` / `studio_style.json`**：开发商风格向量、风格余弦相似度矩阵、风格分层聚类
- **`goty_genre.csv` / `goty_profile.json`**：GOTY vs 其他作品的区分因子（Cohen's d）与类型 Over-index
- `ML_REPORT.md`：含聚类画像、社区画像、上升/下降类型、中心性排名、**开发商风格相似性**、**GOTY 特征分析**等（共七节）
- 12 张 PNG：`factor_correlation.png` / `k_silhouette.png` / `cluster_pca.png` / `cluster_profile.png` / `community_graph.png` / **`community_infomap.png`** / `hotspot_trend.png` / `centrality_top.png` / `studio_similarity_heatmap.png` / `studio_style_scatter.png` / `goty_distinguish.png` / `goty_genre_overindex.png`

> “高频因子”在此指从图结构派生的细粒度截面因子矩阵；原数据没有日内 tick 级时序，年份是最细时间粒度。
> 方法说明：聚类默认**先做 PCA 白化**再 KMeans，以缓解 44 维类型 one-hot 带来的维度灾难；`studio_wins` 由 `is_goty` 派生（标签泄漏），可用 `--exclude-reputation` 关闭；轮廓系数普遍偏低（<0.25），簇为探索性划分而非严谨边界。
> **社区发现**采用**可插拔**策略：默认 **Louvain**（质量指标模块度 Q）作为主方法；若已安装 `infomap` 包，则额外运行 **Infomap（地图方程 Map Equation）** 作补充，报告二者对照（社区数 / 模块度 Q / 编码长度 L）。Infomap 无需 resolution 调参、天然支持层级结构，其编码长度 L 越小越好；本数据两者社区数接近（Louvain 14 vs Infomap 15）、Q 几乎一致（0.6175 vs 0.6173），说明「玩法家族」结构稳健。可用 `--community infomap` 将主方法直接切换为 Infomap。
> 热点统计以 **GOTY 获奖作本身**（每年 1 款、两半段各 10 款，固定样本、无「其他作品」分母偏差）衡量奖项「品味」演变；比较 2006–2015 与 2016–2025 两半段的类型占比（百分点 pp），并用滚动 3 年占比画图。每半段仅 10 款，结论为示意性趋势而非统计推断。

---

## 📜 许可与数据归属

- 代码以 **MIT** 许可证开源（见 [LICENSE](LICENSE)）。
- 游戏元数据为基于公开资料的二次整理，**仅供学习研究**；游戏名称、商标、美术资产归各自权利人所有。如发现数据有误或需补充，欢迎提交 Issue / Pull Request。

---

## 🛠 技术栈

- 前端：原生 HTML/CSS/JS + [vis-network](https://github.com/visjs/vis-network)（力导向图，已本地化）
- 数据：Python 3 合并脚本，输出 CSV（Neo4j 友好）+ JSON
- 部署：nginx 静态托管 / Docker / docker-compose
- 图数据库：Neo4j 5.x
