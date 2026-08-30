# 🎮 近 20 年年度最佳游戏知识图谱（GOTY Knowledge Graph）

一个覆盖 **2006–2025** 年度最佳游戏（Game of the Year）的开放知识图谱：既可**在浏览器里交互式浏览**力导向图谱，也能把整套数据**一键导入 Neo4j** 做图查询分析，还能在网页上调参做**数据挖掘探索**。

> 数据来源：Spike VGA / VGX（2006–2013）+ The Game Awards（2014–2025，含 2025 年黑马《光与影：33 号远征队》）。游戏元数据为基于公开资料的二次整理，仅供学习研究。

---

## ✨ 特性

- **交互式知识图谱网站**：力导向图，点击节点看详情；支持按**年份 / 工作室 / 类型**筛选、关键词搜索、图谱↔表格双视图。
- **🌐 数据探索（探索 SPA）**：内置 FastAPI 探索 API + 原生 ES Module 探索 SPA。打开网站**首先看到原始数据页与洞察页**（v1 只读：图谱 + 表格 + 节点洞察）；点「开始数据探索」才进入探索 SPA——默认直接呈现标准口径的静态分析结果，点「自定义探索」才调参，结果在独立面板、不覆盖默认分析。
- **完整属性**：每款年度最佳游戏都挖掘了类别 / 玩法 / 独特之处 / 缺点·争议 / 评分 / 主要奖项 / 影响力。
- **重点：开发商其他作品**：不仅收录 GOTY，还展开其开发商的**其他代表作**（如 Rockstar 的 GTA 前作与 RDR、FromSoftware 的魂系列），形成「工作室 → 作品」关系网。
- **可导入 Neo4j**：提供标准 CSV 数据集 + 离线 / 在线两种导入方式 + 自动导入脚本。
- **开箱即部署**：探索 SPA 由 FastAPI 同源托管（含原静态图谱页），支持本地 `make serve`、Docker / docker-compose 一键部署；原静态站点也可**双击 `site/index.html`** 离线打开。

---

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

### 方式零：洞察模式（只读，推荐先看这个）

```bash
make insight          # 只读浏览「原始数据页 + 原始洞察页」，默认 http://localhost:8080
```

### 方式一：数据探索 + 原图谱（需后端）

```bash
make serve            # 启动 FastAPI（API + 洞察页 + /explore 探索 SPA），默认 http://localhost:8080
# 原始数据页/洞察： http://localhost:8080/        （默认落地页）
# 探索 SPA：       http://localhost:8080/explore  （调参数做数据挖掘，需主动进入）
```

### 方式二：仅浏览原静态图谱（零后端依赖）

```bash
make serve-static     # 仅静态托管 site/，默认 http://localhost:8080
# 或： cd site && python3 -m http.server 8080
```

也可以**直接双击 `site/index.html`** 离线打开（数据已内联，无需联网；但此方式不含探索 API）。

### 方式三：Docker 托管

```bash
make docker           # 构建镜像 goty-knowledge-graph
make run              # 运行容器，访问 http://localhost:8080
```

### 方式四：全栈（网站 + 可选 Neo4j）

```bash
make up                                  # 默认：web 容器同源托管一切，图后端 networkx
# 原始数据页/洞察： http://localhost:8080/   探索 SPA： http://localhost:8080/explore/
# API：             http://localhost:8080/api/meta
# Neo4j Browser（可选，仅 --profile neo4j 时）： http://localhost:7474
#
# 体验 Neo4j（Cypher 驱动）： .env 设 GOTY_GRAPH_BACKEND=neo4j，再 docker-compose --profile neo4j up -d
```

`docker-compose` 会在 Neo4j 健康检查通过后，由 `importer` 服务自动把数据集导入图库。导入细节见 **[docs/neo4j_tutorial.md](docs/neo4j_tutorial.md)**。

### 方式五：导入到你自己的 Neo4j 实例

如果你已有 Neo4j（非云 Aura），把 `data/csv/` 放到其 `import` 目录后执行 `cypher-shell -u <user> -p <password> -f scripts/init.cypher`；或一键起一个本地 Neo4j 并自动导入：`make neo4j`。详细说明与示例查询见 **[docs/neo4j_tutorial.md](docs/neo4j_tutorial.md)**。

---

## 🌐 数据探索与图谱浏览

探索相关前端功能（参数化探索板块、交互式图谱浏览器、只读图查询接口）的使用说明集中在 **[docs/EXPLORATION.md](docs/EXPLORATION.md)**，包括：

- **参数化探索板块**：社区发现、开发商风格相似、GOTY 品味网络、聚类、时代热点等，可在线调参、实时计算可视化。
- **交互式图谱浏览器**：种子渲染、多跳展开、社区分析（5 种算法 + 同类投影）、网络影响力、最短路径。
- **只读图查询接口** `/api/graph/*`：搜索、节点详情、多跳遍历、最短路径、社区发现、中心性排行榜。

默认 networkx（内存）即完整可用；切换到 Neo4j 后底层查询换 Cypher，接口与响应结构不变。

---

## 🧠 数据挖掘报告

`analysis/` 下是一套可插拔的图数据挖掘流水线（社区发现 / 工作室风格 / GOTY 特征 / 品味网络 / 时代热点等），人读结论见 **[docs/INSIGHTS.md](docs/INSIGHTS.md)**，流水线用法与产物说明见 **[docs/ML_PIPELINE.md](docs/ML_PIPELINE.md)**。

---

## 🔐 安全、认证与运维

对外提供 demo 时的防护（限流 / 黑名单 / UA 拦截 / 异常判定 / 请求审计 / 访问统计）与用户认证体系（注册登录、会话、免登录调试开关、凭据安全）的设计与配置，集中在：

- **[docs/SECURITY.md](docs/SECURITY.md)** — 防护设计、审计、访问控制、认证体系。
- **[docs/CONFIGURATION.md](docs/CONFIGURATION.md)** — 全部 `GOTY_*` 环境变量参考。
- **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** — 部署方式（GitHub Pages 静态站 / 完整站点）、免登录模式、样式构建链。
- **[SECURITY_REPORT.md](SECURITY_REPORT.md)** — 独立漏洞报告（审查结论与残余风险）。

> 部署硬性要求：账号体系依赖 **HTTPS（TLS）** 承载，凭据仅在 TLS 加密通道内传输；生产请把 `GOTY_SESSION_COOKIE_SECURE=true`。

---

## 📁 目录结构（概览）

```
.
├── README.md / LICENSE / Makefile / pyproject.toml / Dockerfile / docker-compose.yml
├── src/                    # 合并原始数据 → 数据集（CSV + graph.json）+ 生成网站
├── data/                   # raw/（原始研究数据）、csv/、neo4j/、graph.json
├── site/                   # index.html（原静态图谱站点，数据内联）+ explorer-graph/（探索 SPA）
├── site/src/               # 样式源（Tailwind 入口，改后需构建）；产物在 site/assets/ 并已提交
├── vendor/                 # 第三方库（vis-network.min.js）
├── scripts/                # init.cypher、serve*.sh、neo4j_import.sh、audit_report.py
├── api/                    # 探索后端（FastAPI，应用工厂 + 路由拆分 + 依赖注入）
├── analysis/               # 可插拔数据挖掘流水线（ml/）
└── docs/                   # 文档（见下方索引）
```

完整后端结构与分层说明见 **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**。

---

## 🛠 技术栈

- **前端**：原静态站点用 [vis-network](https://github.com/visjs/vis-network)（力导向图，已本地化）；探索 SPA 为原生 ES Module，图表用纯 SVG 手绘渲染。样式用 Tailwind v4 构建（`site/src/` → `site/assets/`）——**产物已随仓库提交，因此部署与运行时仍零 Node 依赖**；仅改动样式时才需要 `npm run build:css`（详见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)）。
- **后端**：FastAPI + uvicorn 探索 API；复用 `analysis/ml/` 计算模块（Strategy + Registry 可插拔架构）。
- **数据**：Python 3 合并脚本，输出 CSV（Neo4j 友好）+ JSON。
- **部署**：FastAPI 同源托管 / Docker / docker-compose。
- **图数据库**：Neo4j 5.26（Community，可选后端）。

---

## 📚 文档索引

| 文档 | 内容 |
|------|------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 后端架构、探索 API 设计、双有效性、异步任务、认证分层、模块解耦 |
| [docs/EXPLORATION.md](docs/EXPLORATION.md) | 参数化探索板块、交互式图谱浏览器、只读图查询接口使用指南 |
| [docs/SECURITY.md](docs/SECURITY.md) | 防护设计、请求审计、访问控制、用户认证体系 |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | 全部 `GOTY_*` 环境变量参考 |
| [docs/EMAIL_VERIFICATION.md](docs/EMAIL_VERIFICATION.md) | 邮件验证功能（注册必填邮箱 + 验证前禁登录，已实现；含设计/原理/可行性） |
| [docs/neo4j_tutorial.md](docs/neo4j_tutorial.md) | Neo4j 导入教程（节点/关系模型、两种导入方式、示例查询） |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 如何重新生成 / 扩展数据、修改构建流程 |
| [docs/INSIGHTS.md](docs/INSIGHTS.md) | 数据挖掘报告（奖项品味 / 工作室格局 / 评分门槛 / 研究课题） |
| [docs/ML_PIPELINE.md](docs/ML_PIPELINE.md) | 统计机器学习流水线结构与产物 |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | 部署指南：GitHub Pages 静态站 / 完整站点、免登录模式、前端样式构建链 |
| [SECURITY_REPORT.md](SECURITY_REPORT.md) | 独立漏洞报告 |

---

## 📜 许可与数据归属

- 代码以 **MIT** 许可证开源（见 [LICENSE](LICENSE)）。
- 游戏元数据为基于公开资料的二次整理，**仅供学习研究**；游戏名称、商标、美术资产归各自权利人所有。如发现数据有误或需补充，欢迎提交 Issue / Pull Request。
