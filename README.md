# 🎮 近 20 年年度最佳游戏知识图谱（GOTY Knowledge Graph）

一个覆盖 **2006–2025** 年度最佳游戏（Game of the Year）的开放知识图谱：既可**在浏览器里交互式浏览**力导向图谱，也能把整套数据**一键导入 Neo4j** 做图查询分析。

> 数据来源：Spike VGA / VGX（2006–2013）+ The Game Awards（2014–2025，含 2025 年黑马《光与影：33 号远征队》）。

---

## ✨ 特性

- **交互式知识图谱网站**：力导向图，点击节点看详情；支持按**年份 / 工作室 / 类型**筛选、关键词搜索、图谱↔表格双视图。
- **🌐 交互式数据探索（新增）**：内置 **FastAPI 探索 API + 原生 ES Module 探索 SPA**。用户打开网站**首先看到原始数据页与原始洞察页**（v1 只读：交互式知识图谱 + 表格 + 节点洞察）；点「开始数据探索」才进入探索 SPA——其**默认直接呈现标准口径下的静态分析结果**（无需操作），点「自定义探索」才调参，结果在独立面板、**不覆盖默认分析**。后端仍区分「数据有效性 × 解读有效性」，但前端不告警「解读失效」——用户自行探索只是缺少预设解读，无需显式提示。
- **完整属性**：每款年度最佳游戏都挖掘了类别 / 玩法 / 独特之处 / 缺点·争议 / 评分 / 主要奖项 / 影响力。
- **重点：开发商其他作品**：不仅收录 GOTY，还展开其开发商的**其他代表作**（如 Rockstar 的 GTA 前作与 RDR、FromSoftware 的魂系列、Naughty Dog 的神秘海域等），形成「工作室 → 作品」关系网。
- **可导入 Neo4j**：提供标准 CSV 数据集 + 两种导入方式（离线 `neo4j-admin import` / 在线 `LOAD CSV`）+ 自动导入脚本。
- **开箱即部署**：探索 SPA 由 FastAPI 同源托管（含原静态图谱页），支持本地 `make serve`、Docker / docker-compose 一键部署；原静态站点也可**双击 `site/index.html`** 离线打开。

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

### 方式零：快速启动【洞察模式】（只读，推荐先看这个）

```bash
make insight          # 只读浏览「原始数据页 + 原始洞察页」，默认 http://localhost:8080
# 原始数据页/洞察： http://localhost:8080/        （vis-network 图谱 + 表格 + 节点洞察）
```

### 方式一：数据探索 + 原图谱（需后端，含探索 SPA）

```bash
make serve            # 启动 FastAPI（API + 洞察页 + /explore 探索 SPA），默认 http://localhost:8080
# 原始数据页/洞察： http://localhost:8080/        （默认落地页：vis-network 图谱 + 表格 + 节点洞察）
# 探索 SPA：       http://localhost:8080/explore  （调参数做数据挖掘，需主动进入）
```

### 方式二：仅浏览原静态图谱（零后端依赖）

```bash
make serve-static     # 仅静态托管 site/，默认 http://localhost:8080
# 或：
cd site && python3 -m http.server 8080
```

也可以**直接双击 `site/index.html`** 离线打开（数据已内联，无需联网；但此方式不含探索 API）。

### 方式三：Docker 托管（API + 探索 SPA + 原静态图谱）

```bash
make docker           # 构建镜像 goty-knowledge-graph（python + uvicorn）
make run              # 运行容器，访问 http://localhost:8080
```

### 方式四：全栈（网站 + 自动导入的 Neo4j）

```bash
make up
# 原始数据页/洞察： http://localhost:8080/   探索 SPA： http://localhost:8080/explore
# Neo4j Browser： http://localhost:7474  （用户名 neo4j / 密码 password123）
```

`docker-compose` 会在 Neo4j 健康检查通过后，由 `importer` 服务自动执行 `scripts/init.cypher` 把数据集导入图库。

> 导入细节：`init.cypher` 用 `LOAD CSV FROM 'file:///csv/...'` 读取挂载进来的 CSV。容器已通过 `NEO4J_dbms_directories_import=/import` 把 Neo4j 的 import 目录显式指向挂载点 `/import`（官方镜像默认是 `/var/lib/neo4j/import`，不改会导致找不到文件、导入零节点）。**请勿改动 `data/csv` 的挂载路径 `/import/csv`**。

### 方式五：导入到你自己的 Neo4j 实例

如果你已有 Neo4j（非云 Aura），把 `data/csv/` 放到其 `import` 目录后执行：

```bash
cypher-shell -u <user> -p <password> -f scripts/init.cypher
# 或一键起一个本地 Neo4j 并自动导入：
make neo4j
```

> 详细说明与示例查询见 **[docs/neo4j_tutorial.md](docs/neo4j_tutorial.md)**。

---

## 🌐 交互式数据探索（数据探索 API + 探索 SPA）

在原有静态图谱之外，新增一层**可交互的数据挖掘**界面：用户在网页上调节**算法 / 统计口径参数**，后端实时计算并返回不同的可视化。后端复用 `analysis/ml/` 的计算模块（社区发现 / 随机游走嵌入 / 个性化 PageRank / 特征工程 / 聚类），保证「批量报告」与「交互探索」结论一致、无重复实现。

**四个探索板块（均可调参）：**

| 板块 | 可调参数 | 默认口径下的可视化 |
|------|----------|--------------------|
| **社区发现（玩法家族）** | 方法（louvain / infomap / walktrap）、Louvain 分辨率、Infomap 重复、Walktrap 步数 | 社区网络图 + 规模柱状 + 画像表 |
| **开发商风格相似（双视角）** | 视角（both / sp / rw）、随机游走次数 / 步数 / 窗口 / 嵌入维度 | 相似度热力图 + MDS 风格散点 + Top 对表 |
| **GOTY 品味网络（PPR）** | PageRank 阻尼 α、推荐条数 | 非-GOTY 推荐榜 + 工作室亲和力榜 |
| **聚类（因子画像）** | 算法（kmeans / 层次 / 谱 / DBSCAN）、固定 k、PCA、标准化、含工作室夺冠数 | PCA 散点 + 簇规模柱状 + 簇画像表 |
| **时代热点（奖项品味演变）** | 前后半段分界年 | 类型占比对比柱状 + 上升/下降趋势表 |

**双有效性（后端判定，前端中性呈现）**：当用户调参后，原报告中「默认参数下写好的定性解读」大概率不再成立。后端据此区分两类有效性（仍用于决定解读是否适用），但**前端不再以告警方式提示「失效」**：

1. **数据有效性（data_matches_baseline）**：`data/graph.json` 的 sha256 与文档快照基线 `analysis/_data_baseline.json` 比对。漂移时仅把数据状态标为「数据已更新」，不弹红色告警。
2. **解读有效性（interpretation_valid）**：每个板块声明一组「解读默认值」（如社区发现 = louvain + 分辨率 1.0）。用户把**会改变结论的参数**调离默认值（切换方法 / 改分辨率 / 改 α / 换算法 / 移动分界年…）时，该板块的预写解读**不再适用**——前端在「默认分析」区照常展示标准口径解读（中性标签「标准口径解读」），在「自定义探索」区只展示计算结果、不渲染过期解读，因此不会出现「数据失效 / 解读失效」之类的告警。

> 设计取舍：默认视图 = 标准口径静态分析（带预设解读）；自定义探索 = 用户自己的计算结果（缺预设解读，但不告警）。两者分区展示、互不覆盖。

**架构与扩展：**

```
api/                         # 探索后端（FastAPI）
├── app.py                   # 路由：/api/meta / /api/boards / /api/board/{name}；同源托管 SPA 与静态站点
├── models.py                # ParamSpec（前端据此自动渲染参数控件）+ 校验
├── registry.py              # ExplorationTool 基类 + 注册表 + 双有效性判定
├── graph_loader.py          # 进程级单例：加载 graph.json、构建 G/GG/G_full、sha 守卫
└── tools/                   # 各探索板块（@register 自动发现）
    ├── community.py / studio.py / goty.py / cluster.py / hotspot.py
site/explorer/               # 探索 SPA（原生 ES Module，无构建步骤）
├── index.html / styles.css / app.js
```

- **新增一个探索板块** = 在 `api/tools/` 写一个 `ExplorationTool` 子类（声明参数 schema、解读默认值、并复用 `analysis/ml` 计算），加 `@register`。前端会**自动**生成参数控件、调用、渲染（network/heatmap/scatter/bar 四种纯 SVG 渲染器已内置）。无需改动前端或注册表。
- 后端与前端解耦：面板数据约定为 `{type, title, data}`（`network`/`heatmap`/`scatter`/`bar`）+ `tables` + `metrics` + `validity`，前后端各渲染一次，互不耦合。

---

## 🛡 安全与限流（云端 demo 防护）

探索计算（社区发现 / 嵌入 / PageRank / 聚类）较耗资源，对外提供 demo 时必须防止单个客户端把服务器拖垮。`api/` 内置一层**零依赖**的防护中间件（`api/ratelimit.py` + `api/logging_config.py`）：

- **黑名单（403）**：`GOTY_BLACKLIST` 环境变量种子（逗号分隔，永久封禁）+ 自动封禁（短时内多次超限制即临时封禁）。
- **两档限流（429）**：「一般请求」宽松；「探索计算 `POST /api/board/*`」严格（这是真正耗资源的入口）。超限返回 JSON `{error, message, retry_after}` 并带 `Retry-After` 头。
- **结构化日志**：每条请求记录 `客户端IP / 方法 / 路径 / 状态 / 耗时`；超限与封禁单独告警（WARNING/ERROR），控制台 + 可选滚动文件（`GOTY_LOG_FILE`）。已关闭 uvicorn 自带 access log，避免重复。

**所有阈值通过环境变量配置，无需改代码即可调参（默认值已按云端 demo 取向设定）：**

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_TRUST_PROXY` | 是否信任 `X-Forwarded-For`/`X-Real-IP`（云端 LB/CDN 后务必开；边缘需剥离客户端伪造头） | `true` |
| `GOTY_RATE_LIMIT_MAX` / `GOTY_RATE_WINDOW` | 一般请求限流：每 IP 上限 / 窗口秒 | `200` / `60` |
| `GOTY_BOARD_LIMIT_MAX` / `GOTY_BOARD_WINDOW` | 探索计算限流：每 IP 上限 / 窗口秒 | `8` / `60` |
| `GOTY_AUTOBAN_VIOLATIONS` / `GOTY_AUTOBAN_SECONDS` | 自动封禁：累计超限次数 / 封禁秒数（0=永久） | `5` / `3600` |
| `GOTY_BLACKLIST` | 永久黑名单种子（逗号分隔 IP） | 空 |
| `GOTY_BLACKLIST_FILE` | 自动封禁持久化文件（JSON，重启仍生效） | 空 |
| `GOTY_LOG_LEVEL` / `GOTY_LOG_FILE` | 日志级别 / 日志文件（空=仅控制台） | `INFO` / 空 |

> 多实例部署提示：当前限流/黑名单为**单进程内存版**，适用于单实例 demo。若横向扩展为多副本，请改用共享存储（如 Redis）或把实例数控制在 1，避免各副本计数独立导致实际阈值被放大。

---

## ⚙️ 后台异步任务与探索开关（云端 demo）

### 为什么需要异步任务
社区发现 / 随机游走嵌入 / PageRank / 聚类等都是**耗时计算**。若同步在请求里跑，会阻塞用户、并容易把单进程服务器拖垮。因此探索改为**提交后台任务、前端轮询**：

- `POST /api/jobs` 立即返回 `job_id`（状态 `pending`），计算在线程池后台跑；前端每 1s 轮询 `GET /api/jobs/{id}`，`pending→running→done/failed/canceled` 后渲染结果。**请求绝不阻塞用户流程。**
- 内置**有界线程池**（默认并发 2）提供背压：超额任务排队为 `pending`，天然限制同时运行的重计算；并设**单用户待处理上限**（默认 5），超了直接 `429`，防止队列被刷爆。
- 前端「后台任务」面板列出任务（状态徽章 / 查看结果 / 取消），满足**查看运行状态、运行结果**的后台任务管理需求。

### 排队位次可视化（用户感知背压）
当并发被 `GOTY_TASK_WORKERS` 限制、任务进入 `pending` 排队时，前端会让用户清楚「我排在第几位、前面还有几个在算」，而不是干等：

- 后端 `TaskManager.queue_position(tid)` 返回该任务的 **1-based 位次** = 比它更早创建且仍处于 `pending/running`（仍占用或等待算力）的任务数 + 1；非排队态（`running`/`done` 等）返回 `None`。
- `GET /api/jobs/{id}` 在 `pending` 时附带 `queue_position`，并始终附带全局快照 `queue_running / queue_waiting / queue_max_workers`；`GET /api/jobs` 列表对每个 `pending` 任务同样给出位次。
- 新增轻量端点 `GET /api/jobs/queue` 返回全局队列负荷（运行中 / 等待 / 并发上限），任务面板顶部常驻显示「队列负荷：X 运行中 · Y 等待 · 并发上限 Z」。
- 前端：轮询中若处于 `pending`，提示「排队中…（队列第 N 位 · X/Y 计算中 · 共 Z 个等待）」；任务行对 `pending` 任务显示「第 N 位」徽章。任务开始计算或结束，位次随之前移。

### 探索总开关（默认不开放，避免撑爆服务器）
云端部署通过 `GOTY_ENABLE_EXPLORATION` 控制是否对用户开放数据挖掘/探索：

| 取值 | 行为 |
|------|------|
| `false`（**默认**） | 只读快速模式：根路径 `/` 托管**原第一版** `site/index.html`（vis-network 图谱 + 表格双视图 + 节点洞察），用户只能浏览；`/api/jobs`、`/api/board` 返回 `403`。 |
| `true` | 完整探索：根路径 `/` 同样托管**原 v1（数据页 + 洞察页，默认落地页）**，并额外在 `/explore` 托管探索 SPA（异步任务 + 参数调节 + 任务管理）；v1 工具栏会自动出现「开始数据探索 →」入口。 |

> **本地快速模式**：直接 `make insight` 即为只读 v1（最快、等同第一版效果，仅看原始数据页 + 洞察）。想本地调试探索，用 `make serve`（已自动设 `GOTY_ENABLE_EXPLORATION=true`），或手动 `GOTY_ENABLE_EXPLORATION=true` 启动。

### 轻量令牌门禁（轻量用户管理）
开启探索后，可用 `GOTY_EXPLORE_TOKEN` 限定**谁能提交计算任务**（无需注册/密码/数据库）：

- 未设令牌：开放提交，任务按访客 IP 归属（匿名身份）。
- 设了令牌：提交/列出任务须带 `Authorization: Bearer <token>`（或 `X-Explore-Token` / `?token=`）。前端左侧「访问令牌」框粘贴后即生效（存于 localStorage）。不匹配返回 `401`。
- 持令牌者访问 `GET /api/jobs?scope=all` 可查看**全部**任务，便于后台巡检。

新增环境变量（叠加在安全节之上）：

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_ENABLE_EXPLORATION` | 是否开放数据挖掘/探索模式（默认关，只读浏览） | `false` |
| `GOTY_EXPLORE_TOKEN` | 开启探索后提交任务所需的访问令牌（空=开放匿名） | 空 |
| `GOTY_TASK_WORKERS` | 后台计算线程池并发数（背压） | `2` |
| `GOTY_MAX_PENDING` | 单用户待处理任务上限（超出 429） | `5` |

---

## 📁 目录结构

```
.
├── README.md
├── LICENSE                 # MIT
├── Makefile                # 快捷命令（insight/serve/serve-static/build/docker/run/up/neo4j/analysis/install/lint/test/test-perf/ci）
├── pyproject.toml          # 依赖唯一来源（uv）+ ruff/pytest 配置
├── uv.lock                 # 锁定依赖（CI/Docker 精确安装）
├── requirements.lock.txt   # 运行时依赖锁定清单（供 Docker `uv pip install`）
├── .pre-commit-config.yaml # 提交前钩子（ruff lint + format）
├── .github/workflows/ci.yml# CI：lint + test + 性能门禁（push/PR 触发）
├── Dockerfile              # 数据探索镜像（uv 锁文件安装 + uvicorn，含 API 与原静态站点）
├── docker-compose.yml      # web(API+探索 SPA) + neo4j + 自动导入
├── requirements.txt        # （旧）传统 pip 依赖清单，已被 pyproject/uv 取代
├── .dockerignore
├── src/
│   ├── build.py            # 合并原始数据 → 数据集（CSV + graph.json）
│   └── build_site.py       # 生成交互式网站 site/index.html
├── data/
│   ├── raw/                # 原始研究数据（5 个 agent 的结构化 JSON）
│   ├── csv/                # 普通表头 CSV（供 Cypher LOAD CSV）
│   ├── neo4j/              # 冒号表头 CSV（供 neo4j-admin import）
│   └── graph.json          # 合并后的图谱（供网站 / 检查）
├── site/
│   ├── index.html          # 原静态图谱站点（vis-network 力导向图）
│   └── explorer/           # 探索 SPA（原生 ES Module，无构建步骤）
├── vendor/                 # 第三方库（vis-network.min.js）
├── scripts/
│   ├── init.cypher         # Neo4j 自动导入脚本
│   ├── serve.sh            # 本地静态服务器（仅原站点）
│   ├── serve_api.sh        # 本地启动 API + 探索 SPA + 原静态站点
│   └── neo4j_import.sh     # 单独起 Neo4j 并导入
├── api/                    # 探索后端（FastAPI，应用工厂 + 路由拆分 + 依赖注入）
│   ├── app.py              # create_app() 工厂 + lifespan + 同源托管 SPA / 原静态站点 + 安全中间件
│   ├── config.py           # pydantic-settings 集中 GOTY_* 配置
│   ├── schemas.py          # 类型化响应模型（response_model）
│   ├── deps.py             # 依赖注入（settings/security/task_manager/owner/require_exploration）
│   ├── security.py         # 安全上下文（限流/黑名单/令牌）
│   ├── routers/            # APIRouter 拆分：meta / boards / jobs
│   ├── models.py           # ParamSpec（参数 schema）+ 校验
│   ├── registry.py         # ExplorationTool 基类 + 注册表 + 双有效性判定
│   ├── graph_loader.py      # 图谱加载 + sha 守卫（数据有效性）
│   ├── tasks.py            # 后台异步任务管理器（有界线程池 + 待处理上限 + 归属）
│   ├── ratelimit.py        # 限流 + 黑名单（含自动封禁）+ 客户端 IP 识别
│   ├── logging_config.py   # 结构化请求 / 安全日志
│   └── tools/              # 各探索板块（@register 自动发现）
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

## 🧪 开发与工程化（uv + ruff + pre-commit + CI）

后端已按 FastAPI 最佳实践重构：**应用工厂 `create_app()` + `lifespan`**、**`APIRouter` 按域拆分**（`api/routers/{meta,boards,jobs}`）、**依赖注入 `Depends`**（`api/deps.py`）、**`pydantic-settings` 集中配置**（`api/config.py`）、**类型化 `response_model`**（`api/schemas.py`）。对外接口行为不变，但结构清晰、可测试、易扩展。

**依赖管理（uv）**：`pyproject.toml` 为唯一来源，`uv.lock` 锁定，`uv sync` 一键装好运行时 + 开发依赖（pytest/httpx/ruff/pre-commit/locust）。Docker 与 CI 均按锁文件精确安装。

```bash
make install     # uv sync --extra analysis + 安装 pre-commit 钩子
make lint        # ruff check + ruff format --check
make test        # pytest（正确性 + 安全）
make test-perf   # pytest -m perf（进程内并发压测：p95 / 吞吐门禁）
make ci          # 本地跑一遍 CI 等价步骤（lint + test + perf）
```

**提交前检查**：`.pre-commit-config.yaml` 在 `git commit` 时自动跑 `ruff`（lint + format），未通过则拦截提交。

**接口与性能测试**（`tests/`）：
- `test_meta.py / test_boards.py / test_jobs.py`：元数据、同步板块、异步任务（创建/列表/轮询/取消/排队位次）。
- `test_security.py`：限流、黑名单自动封禁、探索令牌门禁。
- `tests/perf/test_perf.py`：用 `httpx` + `ASGITransport` 在进程内并发打接口，断言 **p95 延迟**与**吞吐量**；`tests/perf/locustfile.py` 供手动大流量压测（`uv run locust -f tests/perf/locustfile.py`）。

**CI/CD**：`.github/workflows/ci.yml` 在 `push`/`PR` 触发，使用 `astral-sh/setup-uv` + Python 3.12，`uv sync --frozen` 后依次执行 ruff 检查、pytest、`-m perf` 性能门禁。推到 GitHub 后即自动生效。

---

## ⚙️ 环境变量（.env）

本项目所有可配置项都收敛为 `GOTY_*` 环境变量（`api/config.py` 的 pydantic-settings，`env_prefix="GOTY_"`），并支持从 `.env` 文件加载。仓库提供 **`.env.sample`** 作为样例；**`.env` 已被 `.gitignore` 忽略，需你本地创建、不会进版本库**：

```bash
cp .env.sample .env      # 然后按需修改里面的默认值
```

启动方式都会自动加载 `.env`：

- `make run`：`docker run --env-file .env`（若 `.env` 不存在会自动从 `.env.sample` 复制）。
- `make up`：`docker-compose` 自动读取 `.env`（`web` 服务 `env_file` + 变量替换；Neo4j 密码经 `${NEO4J_PASSWORD:-...}` 注入）。
- 本地 `make serve` / `make insight`（`uv run`）：因 `api/config.py` 已设 `env_file=".env"`，进程内同样自动读取。

`.env.sample` 中每个变量等号右侧即为其**默认值**；常用项：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `GOTY_ENABLE_EXPLORATION` | `false` | 是否开启探索 SPA（false=只读洞察页） |
| `GOTY_EXPLORE_TOKEN` | 空 | 探索页操作口令（留空=不校验） |
| `GOTY_GRAPH_BACKEND` | `networkx` | 图后端：`networkx`（默认，内存）\| `neo4j`（可选，见下节） |
| `GOTY_RATE_LIMIT_MAX` / `GOTY_BOARD_LIMIT_MAX` | `200` / `8` | 全局 / 板块级限流阈值 |
| `GOTY_AUTOBAN_VIOLATIONS` / `GOTY_AUTOBAN_SECONDS` | `5` / `3600` | 触发自动封禁的违规次数与封禁秒数 |
| `NEO4J_PASSWORD` | `password123` | 仅容器化 Neo4j 用；在 `.env` 中改强密码（compose 的 `neo4j` 与 `importer` 共用） |

---

## 🕸 可选图后端（Neo4j + 更丰富的查询/检索）

后端把「图查询」抽象成 `api/graph_store.GraphStore`，默认用**内存 networkx**（零依赖、离线可用，也就是当前 API 的数据底座）。当你想要 Cypher 式的多跳遍历、路径查询、未来接 GraphRAG 时，可以把后端切换到 **Neo4j**——**默认仍是 networkx，零回退风险**。

**新增的只读图查询接口（无论探索开关是否开启都可访问）：**

| 接口 | 说明 |
| --- | --- |
| `GET /api/graph/search?q=&limit=` | 关键词检索节点（标签 / 标题 / 名称 / 开发商…） |
| `GET /api/graph/node/{id}` | 取单个节点详情 |
| `GET /api/graph/traverse?start=&hops=&types=` | 以某节点为中心做多跳邻居展开（子图，供可视化） |
| `GET /api/graph/path?a=&b=` | 两节点间最短路径 |

> 这些端点默认走 networkx 就已可用；切到 Neo4j 后只是把底层查询换成 Cypher，接口与响应结构不变。

### 切换到 Neo4j（可选）

1. 起一个开发用 Neo4j（**非默认端口**，避免抢占你本机已有实例）：

   ```bash
   make neo4j-dev     # HTTP 7475 / Bolt 7688，容器名 neo4j-goty-dev，自动执行 init.cypher 导入
   # 停止： make neo4j-stop    重新导出 CSV： make neo4j-export
   ```

2. 装 driver 并打开开关（driver 为可选依赖，默认镜像不含）：

   ```bash
   uv pip install ".[neo4j]"                       # 或 pip install neo4j>=5.0
   export GOTY_GRAPH_BACKEND=neo4j
   export GOTY_NEO4J_URI=bolt://localhost:7688
   export GOTY_NEO4J_USER=neo4j
   export GOTY_NEO4J_PASSWORD=password123
   make serve          # 重启后端，/api/meta 的 graph_backend 会显示 neo4j
   ```

**健壮性**：若显式选了 `neo4j` 但连不上（驱动缺失 / 实例没起），工厂会**自动回退**到 networkx 并打告警，API 永不因此整体崩溃；查询时则统一返回 `503 graph_backend_unavailable`。

> **⚠️ 遇到 `Neo4j.ClientError.Security.AuthenticationRateLimit`？**
> 这是 Neo4j 的防暴力破解机制：连续多次用**错误密码**连接后，会临时拒绝一切认证（含正确密码），直到重启容器。根因通常是：
> 1. **改了密码却没重建数据卷**——Neo4j 只在**首次启动**设置密码；若你改过 `.env` 的 `NEO4J_PASSWORD` 而 `neo4j_data` 卷里还是旧密码的库，导入/连接就会一直用新密码撞旧库 → 触发限流。
>    - 恢复：先用**当前**密码重启容器清掉内存限流（`docker-compose restart neo4j` 或 `docker rm -f neo4j-goty-dev`），并重建数据卷使其采纳新密码（`docker-compose down -v` / `docker volume rm neo4j-goty-data` 后重跑）。
> 2. **密码不一致**——compose 的 `neo4j` 与 `importer` 共用 `NEO4J_PASSWORD`；独立脚本 `neo4j_dev.sh` / `neo4j_import.sh` 也读 `.env` 的 `NEO4J_PASSWORD`。把 `.env` 的 `NEO4J_PASSWORD` 作为唯一真值即可三方一致（脚本默认 `password123`，但会自动读 `.env` 覆盖）。
> 导入逻辑已改为「等 Bolt 端口 **TCP 就绪（不发凭据）** → 仅在非认证类瞬时错误时重试，认证错误立即退出」，不会再因轮询把账户锁死。
> 镜像拉不动 Docker Hub 时，可在 `.env` 设 `NEO4J_IMAGE` 改用国内/ARM 镜像（如华为云 `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/neo4j:5.26-linuxarm64`）。

### 长期路线：GraphRAG 基础

当前已具备「单一数据源 → CSV → Neo4j」的同步链路（`src/build.py` 导 CSV、`scripts/init.cypher` 导入）。把后端接到 Neo4j 后，后续可平滑演进：

- **Phase 2（图分析）**：用 Neo4j GDS 的中心性 / 社区发现 / 节点相似，替代或增强现有 `analysis/ml` 自研管线。
- **Phase 3（GraphRAG + Agent）**：在 Neo4j 内建向量索引 + LLM 生成节点 / 社区摘要，再用 Agent 做自然语言探索（涉及 API key / 成本 / 评测，另行决策）。

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
├── community.py     # 社区发现策略注册表（louvain / infomap / walktrap，可插拔）
├── analyzers.py     # Analyzer 基类 + 注册表（聚类 / 社区 / 热点 / 工作室风格 / GOTY 特征 / GOTY 品味网络）
├── visualizers.py   # Visualizer 基类 + 注册表（16 张图）
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
python analysis/run_ml.py --community walktrap           # 或换成 Walktrap（随机游走社区发现）
```

> 依赖见 `analysis/requirements.txt`，建议在隔离 venv 中运行（见 `make analysis`）。`infomap` 为可选依赖（仅 Infomap 方法需要），其余随机游走方法（Walktrap、嵌入、个性化 PageRank）仅用 numpy/networkx/sklearn。

产物写入 `analysis/output/`：
- `factors.csv`：107 款游戏 × 63 列因子（图拓扑 4 / 属性 6 / 声誉 3 / 类型 one-hot 44 / 标识 6）
- `clusters.csv`、`communities.csv`、`communities_infomap.csv`、`communities_walktrap.csv`、`hotspot_era.csv`、`hotspot_year.csv` 及各 `*_profile.json`
- **`studio_similarity.csv` / `studio_style.csv` / `studio_style.json`**：开发商风格（**图谱距离 / 最短路径**视角）相似度矩阵、MDS 风格散点坐标
- **`studio_similarity_rw.csv` / `studio_style_rw.csv`**：同上，但为**随机游走嵌入**视角（两者并存、互为对照；`studio_style.json` 含两种视角的 Top 对与 Spearman 一致性 ρ）
- **`goty_genre.csv` / `goty_profile.json`**：GOTY vs 其他作品的区分因子（Cohen's d）与类型 Over-index
- **`goty_affinity.csv` / `goty_affinity.json`**：GOTY 品味网络——个性化 PageRank 给出的「喜欢 GOTY 还会喜欢…」推荐与工作室亲和力
- `ML_REPORT.md`：含聚类画像、社区画像、上升/下降类型、中心性排名、**开发商风格相似性（图谱距离 + 随机游走嵌入 双视角）**、**GOTY 特征分析**、**GOTY 品味网络**等（共八节）
- 16 张 PNG：`factor_correlation.png` / `k_silhouette.png` / `cluster_pca.png` / `cluster_profile.png` / `community_graph.png` / `community_infomap.png` / **`community_walktrap.png`** / `hotspot_trend.png` / `centrality_top.png` / `studio_similarity_heatmap.png` / `studio_style_scatter.png` / **`studio_similarity_rw_heatmap.png`** / **`studio_style_rw_scatter.png`** / `goty_distinguish.png` / `goty_genre_overindex.png` / **`goty_affinity.png`**

> “高频因子”在此指从图结构派生的细粒度截面因子矩阵；原数据没有日内 tick 级时序，年份是最细时间粒度。
> 方法说明：聚类默认**先做 PCA 白化**再 KMeans，以缓解 44 维类型 one-hot 带来的维度灾难；`studio_wins` 由 `is_goty` 派生（标签泄漏），可用 `--exclude-reputation` 关闭；轮廓系数普遍偏低（<0.25），簇为探索性划分而非严谨边界。
> **社区发现**采用**可插拔**策略：默认 **Louvain**（质量指标模块度 Q）作为主方法；无论主方法为何，**Infomap（地图方程 Map Equation）** 与 **Walktrap（随机游走距离层次聚并）** 两种随机游走方法都作为补充始终运行，报告三者对照（社区数 / 模块度 Q / 编码长度 L）。本数据三者社区数接近（Louvain 14 / Infomap 15 / Walktrap 11）、Q 几乎一致（0.6175 / 0.6173 / 0.5905），说明「玩法家族」结构稳健。可用 `--community {louvain|infomap|walktrap}` 切换主方法。
> **开发商风格（第五节）**用**两种并存视角**度量工作室风格接近度，互为印证而非替换：**A. 图谱距离（最短路径）**——在游戏-游戏投影图 GG 上测工作室间最短路径距离（一阶邻近性，相似度集中在约 0.3~0.7）；**B. 随机游走嵌入**——在完整异构图跑截断随机游走 → 游戏共现矩阵(log1p) → SVD 降维得「游戏嵌入」→ 工作室取均值 → 余弦相似度（二阶/多跳邻近性，相似度更分离，如 Bethesda↔CDPR 0.94、Rockstar↔圣莫尼卡 0.92）。两视角距离排序的 Spearman 相关 ρ≈0.8，高度一致；随机游走只是更平滑的等价尺子，正确定位是与最短路径并列的**探索手段**。
> **GOTY 品味网络（第七节，新增）**：把全部 20 款 GOTY 获奖作作为种子，在完整异构图做**个性化 PageRank**，得到「喜欢 GOTY 的人还会喜欢谁」的推荐网络，天然浮现每家获奖作的「同门兄弟」（如 Bethesda 的辐射系列、CDPR 的赛博朋克）。
> 热点统计以 **GOTY 获奖作本身**（每年 1 款、两半段各 10 款，固定样本、无「其他作品」分母偏差）衡量奖项「品味」演变；比较 2006–2015 与 2016–2025 两半段的类型占比（百分点 pp），并用滚动 3 年占比画图。每半段仅 10 款，结论为示意性趋势而非统计推断。

---

## 📜 许可与数据归属

- 代码以 **MIT** 许可证开源（见 [LICENSE](LICENSE)）。
- 游戏元数据为基于公开资料的二次整理，**仅供学习研究**；游戏名称、商标、美术资产归各自权利人所有。如发现数据有误或需补充，欢迎提交 Issue / Pull Request。

---

## 🛠 技术栈

- 前端：原静态站点用 [vis-network](https://github.com/visjs/vis-network)（力导向图，已本地化）；**探索 SPA 为原生 ES Module（无构建步骤），图表用纯 SVG 手绘渲染（network / heatmap / scatter / bar）**
- 后端：**FastAPI + uvicorn** 探索 API；复用 `analysis/ml/` 计算模块（Strategy + Registry 可插拔架构）
- 数据：Python 3 合并脚本，输出 CSV（Neo4j 友好）+ JSON
- 部署：FastAPI 同源托管 / Docker（python + uvicorn）/ docker-compose
- 图数据库：Neo4j 5.26（Community，可选后端）
