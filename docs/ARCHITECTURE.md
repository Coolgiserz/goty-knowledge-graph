# 架构与设计 · GOTY 知识图谱

本文档说明探索后端与前端的设计取舍、分层结构与扩展方式。面向**开发者 / 运维**，普通用户请直接看 [README](../README.md) 与 [docs/EXPLORATION.md](EXPLORATION.md)。

---

## 1. 整体架构

后端为 **FastAPI + uvicorn** 应用工厂结构，前端为原生 ES Module（无构建步骤）的静态站点与探索 SPA，由后端同源托管。

```
api/                         # 探索后端（FastAPI，应用工厂 + 路由拆分 + 依赖注入）
├── app.py                   # create_app() 工厂 + lifespan + 同源托管 SPA / 原静态站点
├── middleware.py            # 公共中间件工厂 create_security_audit_middleware（与 app 解耦）
├── rules.py                 # 可插拔访问控制规则（AccessRule 协议 + BotUserAgentRule）
├── ua.py                    # User-Agent 解析（设备推断 / 爬虫识别）
├── config.py                # pydantic-settings 集中 GOTY_* 配置
├── schemas.py               # 类型化响应模型（response_model）
├── deps.py                  # 依赖注入（settings / security / task_manager / owner / require_exploration）
├── security.py              # 安全上下文（限流 / 黑名单 / 令牌）
├── routers/                 # APIRouter 拆分：meta / boards / jobs / graph / admin(内部报表) / auth
├── models.py                # ParamSpec（参数 schema）+ 校验
├── registry.py              # ExplorationTool 基类 + 注册表 + 双有效性判定
├── graph_loader.py          # 图谱加载 + sha 守卫（数据有效性）
├── graph_store.py           # GraphStore 抽象（networkx / neo4j 双后端，统一图查询）
├── community.py             # 社区发现策略模式（5 算法 + 教育性动画帧）
├── tasks.py                 # 后台异步任务管理器（有界线程池 + 待处理上限 + 归属）
├── ratelimit.py             # 限流 + 黑名单（RateLimiter 协议 + Limiter 内存实现 + RedisLimiter 参考实现）
├── logging_config.py        # goty.api / goty.audit 日志（按时间轮转，JSON 行）
├── anomaly.py               # 请求源异常判定（AnomalyRule 协议 + FrequencyRule，可插拔）
├── audit/                   # 请求审计 + 站点访问统计（同步 + 异步双接口）
└── tools/                   # 各探索板块（@register 自动发现）
site/
├── index.html               # 原静态图谱站点（vis-network 力导向图，数据内联）
└── explorer/                # 探索 SPA（原生 ES Module，无构建步骤）
```

**后端重构基线（FastAPI 最佳实践）**：`create_app()` 工厂 + `lifespan`、`APIRouter` 按域拆分、`Depends` 依赖注入、`pydantic-settings` 集中配置、类型化 `response_model`。routers 一律经 `app.state.settings`（`Depends get_settings_dep`）读取配置，**不读全局 `get_settings()`**。

---

## 2. 探索后端（Exploration API）设计

在原有静态图谱之外提供一层可交互的数据挖掘界面：用户在网页调参，后端实时计算并返回可视化。后端复用 `analysis/ml/` 的计算模块（社区发现 / 随机游走嵌入 / 个性化 PageRank / 特征工程 / 聚类），保证「批量报告」与「交互探索」结论一致、无重复实现。

### 2.1 双有效性判定（设计取舍）

当用户调参后，原报告中「默认参数下写好的定性解读」大概率不再成立。后端据此区分两类有效性，但**前端中性呈现、不弹告警**：

1. **数据有效性（`data_matches_baseline`）**：`data/graph.json` 的 sha256 与文档快照基线 `analysis/_data_baseline.json` 比对。漂移时仅标「数据已更新」，不弹红色告警。
2. **解读有效性（`interpretation_valid`）**：每个板块声明一组「解读默认值」（如社区发现 = louvain + 分辨率 1.0）。用户把**会改变结论的参数**调离默认值（切换方法 / 改分辨率 / 改 α / 换算法 / 移动分界年…）时，该板块的预写解读不再适用。

> 默认视图 = 标准口径静态分析（带预设解读）；自定义探索 = 用户自己的计算结果（缺预设解读，但不告警）。两者分区展示、互不覆盖。

### 2.2 扩展方式（新增探索板块）

新增一个板块 = 在 `api/tools/` 写一个 `ExplorationTool` 子类（声明参数 schema、解读默认值、并复用 `analysis/ml` 计算），加 `@register`。前端会**自动**生成参数控件、调用、渲染（network / heatmap / scatter / bar 四种纯 SVG 渲染器已内置），无需改动前端或注册表。

### 2.3 前后端解耦约定

面板数据约定为 `{type, title, data}`（`network` / `heatmap` / `scatter` / `bar`）+ `tables` + `metrics` + `validity`，前后端各渲染一次，互不耦合。

---

## 3. 后台异步任务

社区发现 / 随机游走嵌入 / PageRank / 聚类都是**耗时计算**。若同步在请求里跑，会阻塞用户并拖垮单进程服务器。因此探索改为**提交后台任务、前端轮询**：

- `POST /api/jobs` 立即返回 `job_id`（状态 `pending`），计算在线程池后台跑；前端每 1s 轮询 `GET /api/jobs/{id}`，`pending→running→done/failed/canceled` 后渲染结果。
- **有界线程池**（默认并发 2）提供背压：超额任务排队为 `pending`；**单用户待处理上限**（默认 5）超了直接 `429`，防止队列被刷爆。
- 前端「后台任务」面板列出任务（状态徽章 / 查看结果 / 取消）。

### 3.1 排队位次可视化（背压可感知）

当任务进入 `pending` 排队时，后端让前端清楚「排第几位、前面还有几个在算」：

- `TaskManager.queue_position(tid)` 返回 **1-based 位次** = 比它更早创建且仍处于 `pending/running` 的任务数 + 1；非排队态返回 `None`。
- `GET /api/jobs/{id}` 在 `pending` 时附带 `queue_position`，并始终附带全局快照 `queue_running / queue_waiting / queue_max_workers`；`GET /api/jobs/queue` 返回全局队列负荷。

---

## 4. 探索总开关与轻量令牌

云端部署通过 `GOTY_ENABLE_EXPLORATION` 控制是否开放数据挖掘：

| 取值 | 行为 |
|------|------|
| `false`（默认） | 只读快速模式：根路径 `/` 托管 v1 `site/index.html`（图谱 + 表格 + 节点洞察），用户只能浏览；`/api/jobs`、`/api/board` 返回 `403`。 |
| `true` | 完整探索：根路径 `/` 同样托管 v1（默认落地页），并额外在 `/explore` 托管探索 SPA；v1 工具栏自动出现「开始数据探索 →」入口。 |

开启探索后，可用 `GOTY_EXPLORE_TOKEN` 限定谁能提交计算任务（无需注册 / 密码 / 数据库）：

- 未设令牌：开放提交，任务按访客 IP 归属（匿名身份）。
- 设了令牌：提交 / 列出任务须带 `Authorization: Bearer <token>`（或 `X-Explore-Token` / `?token=`）。不匹配返回 `401`。
- 持令牌者访问 `GET /api/jobs?scope=all` 可查看全部任务，便于后台巡检。

> **本地快速模式**：`make insight` 即为只读 v1（最快）。本地调试探索用 `make serve`（已自动设 `GOTY_ENABLE_EXPLORATION=true`）。

---

## 5. 模块解耦边界（图分析）

算法引擎与扩展机制已与游戏域脱钩，唯一摩擦点（节点类型词汇表散落硬编码）已通过 `api/schema.py` 的 `NodeTypeSpec` / `GRAPH_SCHEMA`（单表映射 `group→neo4j_label/summary_fields/…`）外置；`community.py` / `graph_store.py` / `routers/graph.py` 全部读表。新业务仅改一张表即复用全部图分析；业务语义（`graph_loader.py`、`tools/*.py`）逐业务重写，`@register` 即插即用。

---

## 6. 异步改造准则

**异步化 ≠ 删同步**：一律**同步 + 异步双接口并存**。仅把调用点改为 `await`，但保留同步实现（参照 SQLAlchemy `Session` / `AsyncSession`）。落地范式：`api/audit/store.py` 的 `AuditStore`（异步 `AsyncSession`）与 `SyncAuditStore`（同步 `Session`）并列、共享 ORM 模型，工厂 `create_audit_store(url, async_=...)` 按运行环境选型。未来「改异步」需求先确认是否保留同步给非异步调用方。

---

## 7. 用户认证分层（可迭代）

认证代码严格分层，确保后续扩展只动服务层：

- **路由层** `api/routers/auth.py`：只做 HTTP 边界（调服务、写 Cookie、把 `AuthError` 翻译为 `HTTPException`），不直接调存储层。
- **业务服务层** `api/auth/service.py`：收敛全部业务规则与编排（`register_user` / `authenticate` / `create_session_for` / `delete_session`），以 `AuthError` 层级（HTTP 状态码 + 稳定 `detail` 错误码）表达；规则常量 `USERNAME_RE` / `EMAIL_RE` / `PASSWORD_RE` / `PASSWORD_MIN_LEN` 在此层。
- **存储层** `api/auth/store.py`：`UserStore`（异步）/ `SyncUserStore`（同步），与 `api/audit/store.py` 同构。
- **页面层** `api/auth/pages.py`：独立承载登录 / 注册页 HTML（`LOGIN_PAGE_HTML` / `LOGIN_DISABLED_HTML`）。

调用链：路由 → 服务 → 存储；页面独立。后续扩展（邮件验证 / OAuth / 限额 / 审计埋点）只动服务层，不动路由与存储。详见 [docs/SECURITY.md](SECURITY.md)。
