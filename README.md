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
│   ├── run_ml.py           # 统计机器学习流水线入口
│   ├── ml/                 # 高频因子 / 聚类 / 社区发现 / 热点 / 可视化模块
│   └── output/             # 运行产物：CSV / JSON / PNG / ML_REPORT.md
└── docs/
    ├── neo4j_tutorial.md   # Neo4j 导入教程
    ├── INSIGHTS.md         # 数据挖掘报告（奖项品味 / 工作室格局 / 评分门槛 / 研究课题）
    └── DEVELOPMENT.md      # 如何重新生成 / 扩展数据
```
（运行后会生成 `analysis/output/ML_REPORT.md` 与 7 张可视化 PNG。）

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

`analysis/` 下是一套可直接复用的图数据挖掘流水线，输入为 `data/graph.json`：

```
analysis/
├── run_ml.py              # 一键运行全部
└── ml/
    ├── factors.py         # 高频因子（特征工程）
    ├── cluster.py         # KMeans / 层次聚类
    ├── community.py       # Louvain 社区发现
    ├── hotspot.py         # 热点 / 时代演变统计
    └── visualize.py       # 生成 7 张 PNG 可视化
```

运行：

```bash
make analysis
# 或等价
/Users/tarnished/.workbuddy/binaries/python/envs/default/bin/python analysis/run_ml.py
```

产物写入 `analysis/output/`：
- `factors.csv`：107 款游戏 × 57 个因子（图拓扑 / 属性 / 声誉 / 类型 one-hot）
- `clusters.csv`、`communities.csv`、`hotspot_era.csv`
- `ML_REPORT.md`：含聚类画像、社区画像、上升/下降类型、中心性排名等
- 7 张 PNG：`factor_correlation.png` / `cluster_pca.png` / `cluster_profile.png` / `community_graph.png` / `hotspot_trend.png` / `centrality_top.png` / `k_silhouette.png`

> “高频因子”在此指从图结构派生的细粒度截面因子矩阵；原数据没有日内 tick 级时序，年份是最细时间粒度。

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
