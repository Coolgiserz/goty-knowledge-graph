# 配置参考 · GOTY 知识图谱

本项目所有可配置项都收敛为 `GOTY_*` 环境变量（`api/config.py` 的 pydantic-settings，`env_prefix="GOTY_"`），并支持从 `.env` 文件加载。仓库提供 **`.env.sample`** 作为样例；**`.env` 已被 `.gitignore` 忽略，需你本地创建、不会进版本库**：

```bash
cp .env.sample .env      # 然后按需修改里面的默认值
```

启动方式都会自动加载 `.env`：`make run`（`docker run --env-file .env`，`.env` 不存在会自动从 `.env.sample` 复制）、`make up`（docker-compose 读取）、本地 `make serve` / `make insight`（`api/config.py` 已设 `env_file=".env"`）。`.env.sample` 中每个变量等号右侧即为其**默认值**。

---

## 基础与部署

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_ENABLE_EXPLORATION` | 是否开启探索 SPA（false=只读洞察页） | `false` |
| `GOTY_EXPLORE_TOKEN` | 探索页操作口令（留空=不校验） | 空 |
| `GOTY_GRAPH_BACKEND` | 图后端：`networkx`（默认，内存）\| `neo4j`（可选） | `networkx` |
| `GOTY_TRUST_PROXY` | 是否信任 `X-Forwarded-For` / `X-Real-IP`（云端 LB / CDN 后务必开；边缘需剥离客户端伪造头） | `true` |
| `NEO4J_PASSWORD` | 仅容器化 Neo4j 用；在 `.env` 中改强密码（compose 的 `neo4j` 与 `importer` 共用） | `password123` |

---

## 限流 / 黑名单 / 异常判定

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_RATE_LIMIT_MAX` / `GOTY_RATE_WINDOW` | 一般请求限流：每 IP 上限 / 窗口秒 | `200` / `60` |
| `GOTY_BOARD_LIMIT_MAX` / `GOTY_BOARD_WINDOW` | 探索计算限流：每 IP 上限 / 窗口秒 | `8` / `60` |
| `GOTY_AUTOBAN_VIOLATIONS` / `GOTY_AUTOBAN_SECONDS` | 自动封禁：累计超限次数 / 封禁秒数（0=永久） | `5` / `3600` |
| `GOTY_BLACKLIST` | 永久黑名单种子（逗号分隔 IP） | 空 |
| `GOTY_BLACKLIST_FILE` | 自动封禁持久化文件（JSON，重启仍生效） | 空 |
| `GOTY_RATE_LIMIT_REDIS_URL` | 限流后端：非空则走 Redis（需先 `uv pip install redis`），否则默认内存版 | 空 |
| `GOTY_ANOMALY_ENABLED` | 是否开启请求源异常判定 | `true` |
| `GOTY_ANOMALY_FREQUENCY_MAX` / `GOTY_ANOMALY_FREQUENCY_WINDOW` | 频率规则：单 IP 窗口内最多请求数 / 窗口秒 | `60` / `60` |
| `GOTY_ANOMALY_BAN_SECONDS` | 频率规则命中后的封禁时长（秒，默认 24h） | `86400` |

---

## 访问控制（UA 拦截）

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_BLOCK_BOT_UA` | 是否启用「拦截爬虫 UA」（命中黑名单或空 UA 直接 403；默认开启，仅放行真实浏览器；`/api/admin` 前缀豁免；服务间调用可设 `false`） | `true` |
| `GOTY_BOT_UA_BLOCKLIST` | 禁用的 UA 子串（逗号分隔；命中即 403） | `python,java,go-http,golang,curl,wget,httpx,requests,scrapy,aiohttp,okhttp,guzzle,node,perl,ruby,php,bot,spider,crawl,slurp,headless,scraper,axios,urllib` |

---

## 请求审计 / 访问统计

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_AUDIT_ENABLED` | 是否开启请求审计（文件 + 数据库） | `true` |
| `GOTY_AUDIT_LOG_FILE` | 审计日志文件（按时间周期轮转，每行一条 JSON；空=不写文件，仅入库） | 空 |
| `GOTY_AUDIT_ROTATE_WHEN` / `GOTY_AUDIT_ROTATE_BACKUP` | 轮转单位（`midnight` / `H` / `D`…）/ 保留备份份数 | `midnight` / `14` |
| `GOTY_AUDIT_DB_URL` | 审计数据库 SQLAlchemy URL（换 MySQL / OLAP 仅改此值） | `sqlite:///./data/audit.db` |
| `GOTY_AUDIT_DB_ECHO` | 打印审计 SQL（调试用） | `false` |
| `GOTY_AUDIT_BODY_MAX_BYTES` | 请求体 / 响应体截断上限（字节） | `8192` |
| `GOTY_ADMIN_TOKEN` | 内部管理接口 `GET /api/admin/report` 的访问令牌；留空 = 接口整体禁用（返回 403），不下发前端 | 空 |

---

## 后台异步任务

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_ENABLE_EXPLORATION` | 是否开放数据挖掘 / 探索模式（默认关，只读浏览） | `false` |
| `GOTY_EXPLORE_TOKEN` | 开启探索后提交任务所需的访问令牌（空=开放匿名） | 空 |
| `GOTY_TASK_WORKERS` | 后台计算线程池并发数（背压） | `2` |
| `GOTY_MAX_PENDING` | 单用户待处理任务上限（超出 429） | `5` |

---

## 用户认证

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_AUTH_ENABLED` | 是否开启账号体系；`false` = 「全部免登录」本地调试模式 | `true` |
| `GOTY_AUTH_REGISTRATION_OPEN` | 是否开放自助注册；`false` 时注册接口整体 `403` | `true` |
| `GOTY_EXPLORE_REQUIRES_AUTH` | 探索页是否需要登录方可进入 | `true` |
| `GOTY_USERS_DB_URL` | 用户库 SQLAlchemy URL | `sqlite:///./data/users.db` |
| `GOTY_USERS_DB_ECHO` | 打印用户库 SQL（调试用） | `false` |
| `GOTY_SESSION_TTL_SECONDS` | 会话有效期（秒） | `604800` |
| `GOTY_SESSION_COOKIE_NAME` | 会话 Cookie 名（HttpOnly + SameSite=Lax） | `goty_session` |
| `GOTY_SESSION_COOKIE_SECURE` | Cookie 仅经 HTTPS 下发（生产 https 部署设 `true`） | `false` |

---

## 日志

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_LOG_LEVEL` / `GOTY_LOG_FILE` | 应用日志级别 / 日志文件（空=仅控制台，按大小滚动） | `INFO` / 空 |

> 多实例部署提示：限流 / 黑名单 / 异常计数为单进程内存版，审计库默认单实例 SQLite。横向扩展时把限流经 `GOTY_RATE_LIMIT_REDIS_URL` 切到 Redis，并把审计存储改用共享后端（`GOTY_AUDIT_DB_URL` 换成对应 DSN），或把副本数控制在 1。
