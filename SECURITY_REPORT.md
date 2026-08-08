# GOTY 知识图谱 · 接口安全审查报告

| 项 | 内容 |
| --- | --- |
| 审查对象 | GOTY 知识图谱数据探索 API（FastAPI） |
| 审查版本 | v1.7.2（含 v1.7.1 安全加固与 v1.7.2 可维护性收敛） |
| 审查日期 | 2026-08-08 |
| 审查范围 | HTTP 接口层：请求校验、目录遍历、注入（SQL / Cypher / 命令 / 路径）、认证授权、速率限制、UA 拦截、审计可靠性 |
| 审查方式 | 静态代码审查（人工走读 `api/` 全部接口与中间件路径）+ 单元测试回归 |
| 结论 | **未发现可利用的高危注入 / 目录遍历漏洞**；已修复 5 类隐患（参数越界、空 UA 未拦截、`/api/admin` 被误伤、审计写入致 500、SQLite 并发锁）；UA 拦截默认开启 |

> 说明：本报告为**独立文档**，不替代 `README.md` 的运行说明，也不暴露任何内部实现术语给最终用户。所有引用均指向仓库内源码，便于复核。

---

## 1. 概述与结论

GOTY 知识图谱后端是一套以「只读图谱查询 + 可选数据探索计算」为核心的 API。我们针对用户最关心的三类风险（**请求校验、目录遍历、SQL 注入**）以及周边的安全控制（认证、限流、UA 拦截、审计）做了端到端走读。

**核心结论：**

- **注入类（SQL / Cypher / 命令 / 路径注入）—— 无风险。** 关系型审计库全走 SQLAlchemy ORM / 参数化；可选的 Neo4j 图查询层全部使用 Cypher 参数绑定（`$q/$id/$a/$b/$types/$tags`），唯一一处插值（`hops`）在插值前已被整数范围裁剪到 `[1,4]`；后端不接收任何用于拼文件路径的用户输入。
- **目录遍历 / 任意文件读取 —— 无风险。** 静态资源统一由 Starlette `StaticFiles` 托管，路径经其规范化处理；没有任何接口用请求参数拼接磁盘路径。
- **请求校验 —— 已加固。** 分页 `limit/offset`、图分析 `scope`、影响力 `metric/top_n` 均有白名单或上下界校验；探索计算总开关默认关闭，关闭时计算接口一律 403。
- **UA 拦截 —— 默认开启（v1.7.1）。** 拦截命中黑名单的爬虫 / 脚本 UA 与空 UA；对 `/api/admin` 前缀豁免，由独立令牌守卫鉴权，避免运维脚本被误伤。

仍存在若干**中低风险与可维护性**问题，详见第 7 节「残余风险与后续建议」。

---

## 2. 审查范围与方法

### 2.1 覆盖范围

| 模块 | 文件 | 关注点 |
| --- | --- | --- |
| 应用工厂 | `api/app.py` | 中间件装配、静态挂载、版本 |
| 安全 + 审计中间件 | `api/middleware.py` | 访问控制 → 黑名单 → 限流 → 异常判定 → 审计 全链路 |
| 依赖注入 | `api/deps.py` | 探索开关守卫、令牌解析、归属解析 |
| 访问控制规则 | `api/rules.py` | 爬虫 UA 拦截规则 |
| 安全原语 | `api/security.py`、`api/ratelimit.py`、`api/anomaly.py` | 黑名单、限流、异常频率判定 |
| 配置 | `api/config.py` | 开关与令牌默认值 |
| 图存储 | `api/graph_store.py` | Cypher 参数绑定、`hops` 处理、`list_nodes` 裁剪 |
| 图查询路由 | `api/routers/graph.py` | 查询 / 遍历 / 最短路径 / 社区 / 影响力 |
| 任务路由 | `api/routers/jobs.py` | 任务提交 / 轮询 / 取消 |
| 板块路由 | `api/routers/boards.py` | 同步计算（受总开关约束） |
| 内部管理路由 | `api/routers/admin.py` | 访问统计报表（令牌守卫） |
| 审计存储 | `api/audit/store.py` | ORM 层、WAL / busy_timeout |

### 2.2 方法

- **数据流走读**：从 `Request` 进入，逐层追踪用户输入（查询参数、路径参数、请求体、Header）是否进入任何「拼接」上下文（SQL 字符串、Cypher 字符串、文件路径、命令、重定向目标）。
- **信任边界标注**：明确区分「受信边界内」（`GRAPH_SCHEMA` 配置表、常量、`scope_ids()` 白名单）与「外部输入」（HTTP 参数、UA）。
- **回归验证**：以既有测试套件（`tests/`，含 UA 拦截、审计、限流用例）确认修复不引入行为回退。

---

## 3. 威胁模型

| 威胁主体 | 目标 | 现有缓解 |
| --- | --- | --- |
| 自动化扫描器 / 恶意爬虫 | 抓取接口、消耗资源、探测漏洞 | UA 拦截（默认开）+ 限流 + 异常频率拉黑 |
| 资源耗尽（DoS） | 高频提交探索计算 / 大分页拖垮服务 | 通用限流 + 板块级限流 + `limit` 上界(200) + 异步任务队列(`max_pending`) |
| 越权访问内部报表 | 未授权读取 `GET /api/admin/report` | 令牌守卫（未配令牌整体 403；令牌用 `hmac.compare_digest` 常量时间比较） |
| 注入攻击 | 通过参数注入 SQL / Cypher / 路径 | ORM + 参数化 + 白名单 + 无文件路径拼接 |
| 审计数据丢失 / 污染 | 审计写入失败导致主响应 500 或被绕过 | 审计写入 best-effort 容错 + SQLite `busy_timeout`/WAL |

---

## 4. 发现详情

### 4.1 注入类（结论：无风险）

#### 4.1.1 关系型数据库（审计库）—— 参数化，无拼接

审计存储（`api/audit/store.py`）基于 SQLAlchemy 2.x ORM，所有写操作通过 ORM 模型与 `Session` 提交，**不存在字符串拼接 SQL**。用户输入（IP、UA、路径、请求体片段等）作为 ORM 字段值传入，驱动层自动参数化。

#### 4.1.2 Neo4j 图查询（可选后端）—— 参数绑定 + 单点整数裁剪

`api/graph_store.py` 中所有 Cypher 语句使用命名参数：

- `search`：`WHERE ... CONTAINS $q`（行 616–619、750–756）
- `get_node`：`WHERE n.game_id = $id OR ...`（行 630–631）
- `neighbors`：`$id`、`$types`、以及 `hops` 经 `*1..{hops}` 插值（行 644–655）
- `shortest_path`：`$a`、`$b`（行 689–690）
- `filter`：`$tags` 通过 `UNWIND $tags` 展开（行 846）

**唯一一处插值 `hops`（行 649）** 在插值前被强制裁剪：

```python
hops = max(1, min(hops, 4))          # 行 642：整数范围 [1,4]
cypher = f"MATCH p = (c)-[*1..{hops}]-(n) ..."
```

插值值是受控整数，无法注入任意 Cypher 语法；其余全部为绑定参数。Cypher 注入风险：**无**。

#### 4.1.3 命令注入 / 路径注入 —— 无入口

后端不调用任何 shell / 子进程；`group` 等维度参数经 `api/schema.py` 的 `GRAPH_SCHEMA` 白名单校验（见 4.3.2），不进入文件或命令上下文。

### 4.2 目录遍历与任意文件读取（结论：无风险）

静态资源（`site/`、`site/explorer-graph/`）统一由 `api/app.py` 通过 Starlette `StaticFiles(directory=..., html=True)` 挂载。Starlette 会对请求路径做规范化（解析 `..`、归一化分隔符），**不存在用 URL 路径拼接本地文件系统路径的自定义代码**。全仓搜索确认：无任何接口用请求参数构造文件路径。目录遍历风险：**无**。

### 4.3 请求校验与参数越界（结论：已加固）

| 参数 | 接口 | 校验 | 位置 |
| --- | --- | --- | --- |
| `limit` / `offset` | `GET /api/graph/list` | `limit=max(1,min(limit,200))`，`offset=max(0,offset)`；`NetworkXStore.list_nodes` 与 `Neo4jStore.list_nodes` 后端层再次兜底裁剪 | `routers/graph.py:105-106`、`graph_store.py` |
| `scope` | `GET /api/graph/communities` | 必须等于 `scope_ids()` 白名单（来自 `GRAPH_SCHEMA`），否则 400 | `routers/graph.py:202-206` |
| `metric` | `GET /api/graph/influence` | 仅 `degree/pagerank/betweenness` 之一，否则回退 `pagerank` | `routers/graph.py:248-249` |
| `top_n` | `GET /api/graph/influence` | `max(1,min(int(top_n),100))` | `routers/graph.py:250` |
| `hops` | `GET /api/graph/{traverse,seed,filter}` | 经后端整数裁剪（见 4.1.2） | `graph_store.py` |
| 探索总开关 | `/api/board/*`、`/api/jobs/*` | `require_exploration`：关闭时一律 403 `exploration_disabled` | `deps.py:44-47` |

> **v1.7.1 已修复**：`NetworkXStore.list_nodes` 原先缺少上界裁剪，已被补齐（与 Neo4j 后端一致）；`/api/graph/list` 路由层额外做边界校验，形成 defense-in-depth。

### 4.4 认证与授权

| 接口 | 保护 | 实现 |
| --- | --- | --- |
| `GET /api/admin/report` | 令牌守卫 | 未配 `GOTY_ADMIN_TOKEN` → 整体 403 `admin_report_disabled`；配了则 `hmac.compare_digest` 常量时间比较，失败 401 `invalid_admin_token` | `routers/admin.py:32-39` |
| 探索计算（`POST /api/jobs`、`POST /api/board/{name}`） | 可选令牌 | 配了 `GOTY_EXPLORE_TOKEN` → 必须携带匹配令牌，否则 401 `unauthorized`；未配 → 退化为按 `x-user-id` / 客户端 IP 的匿名身份 | `deps.py:62-82`、`jobs.py:51-57` |
| 任务归属 | 归属校验 | 非管理员只能访问 / 取消自己的任务，越权返回 404 `任务不存在`（避免泄露任务存在性） | `jobs.py:113-114,131-133` |

### 4.5 速率限制 / 暴力破解防护 / UA 拦截

- **通用限流**：`security.general_limiter` 按客户端 IP 限流；超限返回 429 `rate_limited` + `Retry-After`（中间件统一处理）。
- **板块级限流**：仅对探索计算 `POST /api/board/*` 额外限流，保护重资源路径。
- **自动拉黑**：限流累计违规达 `autoban_violations` 次，由 `security.blacklist.register_violation` 临时封禁 `autoban_seconds` 秒。
- **异常频率判定**：`AnomalyDetector` + `FrequencyRule` 对每个 API 请求计数，命中即拉黑（`anomaly_ban_seconds`）。
- **UA 拦截（默认开启，`GOTY_BLOCK_BOT_UA=true`）**：`BotUserAgentRule` 拦截命中黑名单的爬虫 / 脚本 UA（如 `python`/`java`/`go-http` 系）与**空 UA**；对 `/api/admin` 前缀豁免，确保运维 `curl` / 脚本调内部报表时只受令牌守卫约束、不被 UA 误伤（`app.py:90-100`、`rules.py:54-64`）。

### 4.6 审计与日志完整性

- **双写**：每条受审计请求同时落①时间轮转文件（`goty.audit` JSON 行，便于 ELK / 数仓）与②数据库（`AuditStore` ORM）。
- **best-effort 容错（v1.7.1 已修复）**：审计 DB 写入包 `try/except`，**失败仅记日志、不影响主响应**——避免历史上「审计库抖动把已确定的 403/200 变成 500」的问题（`middleware.py:256-262`）。
- **并发可靠性（v1.7.1 已修复）**：文件型 SQLite 追加 `?timeout=30`（`busy_timeout=30s`，sync / aiosqlite 通用），同步引擎再启用 WAL，显著降低「数据库被锁」（`database is locked`）导致的审计丢失。

---

## 5. 已修复的安全隐患（v1.7.1 加固）

| # | 隐患 | 风险 | 修复 |
| --- | --- | --- | --- |
| H1 | `NetworkXStore.list_nodes` 分页无上界裁剪 | 超大 `limit` 可拖垮内存 / 响应 | 后端层 `limit=max(1,min(limit,200))` + 路由层再校验 |
| H2 | 空 UA 未被拦截 | 扫描器 / 恶意工具易绕过 | `BotUserAgentRule(block_empty_ua=True)` 默认拦截空 UA |
| H3 | `/api/admin` 被 UA 拦截误伤 | 运维脚本无法正常取内部报表 | 增加 `exempt_prefixes=["/api/admin"]` 豁免 |
| H4 | 审计写入异常导致主响应 500 | 审计库抖动使正常请求变 500 | 审计写入包 `try/except` best-effort 容错 |
| H5 | SQLite 并发锁 `database is locked` | 高并发下审计记录丢失 / 偶发错误 | `busy_timeout=30s` + 同步引擎 WAL |
| H6 | UA 拦截默认关闭 | 默认部署下爬虫 / 脚本可随意调用 | `GOTY_BLOCK_BOT_UA` 默认值改为 `true`（默认开启） |

---

## 6. 可维护性改进（v1.7.2：状态码 / 错误码集中化）

> 用户指出：HTTP 状态码此前均为硬编码魔法值（`403/503/404/...`），且错误标识字符串存在大量重复（如 `graph_backend_unavailable` 出现 9 次、`任务不存在` 出现 4 次），不利于全局审查与统一修改。

新增 `api/constants.py`：

- `class HTTP(IntEnum)`：集中 `OK/BAD_REQUEST/UNAUTHORIZED/FORBIDDEN/NOT_FOUND/TOO_MANY_REQUESTS/TEMPORARY_REDIRECT/SERVICE_UNAVAILABLE`，取代全部 `status_code=数字`。
- `class ErrorCode`：集中全部错误标识字符串（`graph_backend_unavailable`、`node_not_found`、`no_path`、`任务不存在`、`blocked`、`blacklisted`、`rate_limited`、`exploration_disabled`、`admin_report_disabled`、`invalid_admin_token`、`audit_store_unavailable`、`invalid_or_missing_token`、`unauthorized`、`too_many_pending` 等），复合提示（如 `未知探索板块: xxx`）抽取 `UNKNOWN_BOARD` 前缀复用。

已替换范围：`middleware.py`、`app.py`、`deps.py`、`routers/{boards,admin,jobs,graph}.py`。**行为零变更**，仅消除魔法值；后续新增接口统一从 `api.constants` 取用。

---

## 7. 残余风险与后续建议

| 风险 | 等级 | 建议 |
| --- | --- | --- |
| 限流后端默认内存，多实例不共享 | 中 | 配置 `GOTY_RATE_LIMIT_REDIS_URL` 接入 Redis（工厂已就绪）；或多副本前置共享限流网关 |
| 审计库默认本地 SQLite，非高可用 | 低 | 生产可换 PostgreSQL / OLAP（仅改 `GOTY_AUDIT_DB_URL`，ORM 层兼容）；高并发写建议独立库 |
| `GOTY_ADMIN_TOKEN` / `GOTY_EXPLORE_TOKEN` 强度依赖部署 | 中 | 文档强调使用高熵随机令牌；建议支持密钥管理（如环境变量注入 / Secret 服务），避免写入镜像 |
| CORS 默认 `allow_origins=["*"]` | 中 | 若面向公网，按域名收紧 `allow_origins`；当前为只读图谱 + 内部接口，风险可控 |
| 依赖供应链 | 低 | 已用 `uv.lock` 锁定；建议接入依赖漏洞扫描（如 `pip-audit` / Dependabot）纳入 CI |
| 异常详情返回 | 低 | 图分析 400 会回传策略注册表异常原文（`detail=str(exc)`），属内部友好提示；若对外暴露建议脱敏 |
| 请求体审计截断 | 低 | `audit_body_max_bytes` 默认截断，敏感字段（令牌）已在 `extract_token` 阶段不入库，符合预期 |

---

## 8. 测试与验证

- **单元 / 集成测试**（`tests/`，pytest）：覆盖 UA 拦截（命中 / 空 UA / 浏览器放行 / `/api/admin` 豁免 / 开关关闭）、审计写入、限流、鉴权守卫、图查询错误码等。
- **性能测试**（`tests/perf`，标记 `perf`）：异步并发压测 p95 / 吞吐；已隔离并发锁与关闭 UA 拦截以避免环境干扰。
- **门禁**：改动后端须 `make lint && make test && make test-perf` 全绿（ruff：`E/F/I/B/UP/W/C4/SIM/RUF`，行宽 100；pre-commit 提交前钩子）。
- 本次 v1.7.2 改动（状态码集中化）经上述套件回归，行为无回退。

---

## 附录 A：安全相关配置项（默认）

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `GOTY_BLOCK_BOT_UA` | `true` | 默认开启爬虫 / 空 UA 拦截 |
| `GOTY_BOT_UA_BLOCKLIST` | 内置黑名单 | 命中即拦截的 UA 子串 |
| `GOTY_ENABLE_EXPLORATION` | `false` | 探索计算总开关（关闭时计算接口 403） |
| `GOTY_EXPLORE_TOKEN` | 空 | 探索令牌（空 = 匿名身份） |
| `GOTY_ADMIN_TOKEN` | 空 | 内部报表令牌（空 = 报表整体 403） |
| `GOTY_RATE_LIMIT_*` | 见 `config.py` | 通用 / 板块限流阈值 |
| `GOTY_ANOMALY_*` | 见 `config.py` | 异常频率判定与自动拉黑时长 |
| `GOTY_AUDIT_*` | 见 `config.py` | 审计开关 / 库地址 / 并发参数 |

## 附录 B：关键文件索引

- 安全 + 审计中间件：`api/middleware.py`
- 应用工厂 / 静态挂载：`api/app.py`
- 依赖守卫 / 令牌解析：`api/deps.py`
- UA 拦截规则：`api/rules.py`
- 安全原语（黑名单 / 限流 / 异常）：`api/security.py`、`api/ratelimit.py`、`api/anomaly.py`
- 图查询与 Cypher 绑定：`api/graph_store.py`
- 图 / 任务 / 板块 / 管理路由：`api/routers/{graph,jobs,boards,admin}.py`
- 审计存储：`api/audit/store.py`
- 常量（状态码 / 错误码）：`api/constants.py`（v1.7.2）
