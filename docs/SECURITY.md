# 安全与运维防护 · GOTY 知识图谱

本文档汇总对外提供 demo 时的防护设计、请求审计、访问控制与用户认证体系。面向**运维 / 安全审计**。独立漏洞报告见仓库根 [SECURITY_REPORT.md](../SECURITY_REPORT.md)；环境变量的完整取值见 [docs/CONFIGURATION.md](CONFIGURATION.md)。

---

## 1. 防护总览

探索计算（社区发现 / 嵌入 / PageRank / 聚类）较耗资源，对外提供 demo 时必须防止单个客户端拖垮服务器。防护与审计抽成**独立公共模块** `api/middleware.py`（`create_security_audit_middleware` 工厂，与具体 app 解耦、可被任意 ASGI app 复用），内部按 **访问规则 → 黑名单 → 限流 → 异常判定 → 审计落库** 顺序横切；采用 FastAPI 原生 `@app.middleware("http")` 函数中间件（非 `BaseHTTPMiddleware`），审计数据库写入为**原生 async**（`await audit_store.record_audit`），不阻塞事件循环。底层原语分散在 `api/ratelimit.py` / `api/ua.py` / `api/rules.py` / `api/anomaly.py` / `api/audit/*` / `api/logging_config.py`。

---

## 2. 访问控制（403，可插拔规则）

`GOTY_BLOCK_BOT_UA=true`（**默认开启**）时，凡 User-Agent 命中 `GOTY_BOT_UA_BLOCKLIST`（默认含 `python` / `java` / `go-http` / `curl` / `httpx`…）或**为空**（扫描器特征）的请求直接 403，实现「只放行真实浏览器」。内部报表接口 `/api/admin` 前缀**豁免** UA 拦截，由令牌守卫独立鉴权，避免运维 `curl` / 脚本被误伤。规则实现 `AccessRule` 协议，新增策略（地域封禁、UA 指纹库…）零改中间件；调用方都是非浏览器脚本时可设 `false` 关闭。

---

## 3. 黑名单（403）

`GOTY_BLACKLIST` 环境变量种子（逗号分隔，永久封禁）+ 自动封禁（短时内多次超限制即临时封禁）。

---

## 4. 两档限流（429，可替换后端）

「一般请求」宽松；「探索计算 `POST /api/board/*`」严格（真正耗资源的入口）。超限返回 JSON `{error, message, retry_after}` 并带 `Retry-After` 头。限流原语抽象为 `RateLimiter` 协议，默认内存 `Limiter`；配置 `GOTY_RATE_LIMIT_REDIS_URL` 即无缝换 Redis，调用方无感知。

---

## 5. 请求审计日志（双写）

每条 `/api/*` 请求同时写入：

1. **按时间周期轮转**的审计文件（`GOTY_AUDIT_LOG_FILE`，每行一条 JSON，便于 ELK / 数仓采集）；
2. **数据库**（SQLAlchemy ORM，默认 SQLite 打通流程）。

记录字段含 `客户端 IP / 客户端设备 / User-Agent / 方法 / 接口 / 查询参数 / 请求体 / 状态码 / 耗时 / 是否异常 / 异常原因 / 响应摘要`，以及访问统计维度 `visitor_id（sha256(ip|UA)[:16]）/ referer / route_type（page|api|asset）`。审计存储模块（`api/audit/store.py`）**同时提供同步与异步两套接口**：`AuditStore`（异步，中间件 `await` 调用）与 `SyncAuditStore`（同步，运维脚本 / CLI 使用），共享同一套 ORM 模型，经工厂 `create_audit_store(url, async_=...)` 选型。审计 DB 写入为 best-effort 容错（失败不打断正常响应）。

---

## 6. 站点访问统计（内部，不面向用户）

审计埋点从「仅接口」放宽为「页面 + 接口」——按响应 `Content-Type` 判定 `route_type`，HTML 页面计为 `page`（即 **PV**）、`/api/*` 计为 `api`，静态资源（css / js / 图片 / 字体）计为 `asset` 且**不入审计库、不计 PV**。访客标识用 `sha256(ip|UA)[:16]` 指纹，**不下发 Cookie**；UV 按指纹去重。聚合报表由 `api/audit/report.py` 提供，经①内部接口 `GET /api/admin/report`（`GOTY_ADMIN_TOKEN` 鉴权，未配置则整体禁用）②运维 CLI `python scripts/audit_report.py` 两种方式查看。

---

## 7. 请求源异常判定（可插拔）

`api/anomaly.py` 默认提供「频率规则」——同一 IP 在 `GOTY_ANOMALY_FREQUENCY_WINDOW` 秒内请求数超过 `GOTY_ANOMALY_FREQUENCY_MAX` 即判异常，命中后委托黑名单封禁 `GOTY_ANOMALY_BAN_SECONDS`（默认 24h）。新增策略（UA 异常 / 路径扫描 / 突发分布…）只需实现 `AnomalyRule` 协议并注册，中间件零改动。

---

## 8. 多实例部署提示

当前限流 / 黑名单 / 异常计数为**单进程内存版**，审计库默认也是**单实例 SQLite**，适用于单副本 demo。若横向扩展为多副本：将限流经 `GOTY_RATE_LIMIT_REDIS_URL` 切到 Redis 共享计数，并将审计存储改用共享后端（`GOTY_AUDIT_DB_URL` 直接换成 MySQL / OLAP 的 DSN 即可，ORM 模型与接口不变），或把副本数控制在 1。

---

## 9. 用户认证体系

默认开启一套**服务端账号体系**，让「谁在探索」可审计、可追责。详见 [docs/ARCHITECTURE.md](ARCHITECTURE.md) 第 7 节的分层结构。

### 9.1 核心机制

- **注册 / 登录 / 登出 / 当前用户**：`POST /api/auth/register`（成功即自动登录）、`POST /api/auth/login`、`POST /api/auth/logout`、`GET /api/auth/me`。
- **服务端会话**（非 JWT、无客户端状态）：登录后下发 **HttpOnly + SameSite=Lax** 的会话 Cookie，仅持随机会话 id（`secrets.token_urlsafe(32)`）；会话记录存数据库，支持**过期（默认 7 天）与主动吊销（登出即删行）**。密码用 **bcrypt** 加盐哈希，校验在服务端完成。
- **探索页登录门禁**：`GOTY_EXPLORE_REQUIRES_AUTH=true`（默认）时，未登录访问 `/explore` 由守卫中间件 307 跳转到内置登录页 `/login?next=...`；登录页与 `/api/auth/*` 永远放行，避免死循环。
- **计算 / 提交接口登录门禁**：`POST /api/jobs`、`POST /api/board/{name}` 经 `require_user` 依赖强制登录（未登录 `401 authentication_required`）。登录用户的任务 `owner` 记为其用户名，便于按用户维度追溯。
- **审计记录用户身份**：中间件对已登录请求解析会话用户，把 `user_id` / `username` 写入审计记录（存量审计库自动 ALTER 加列）。

### 9.2 「全部免登录」调试开关

`GOTY_AUTH_ENABLED=false` 即关闭整个账号体系——不要求任何登录、`/explore` 与计算接口直接放行、界面**隐藏登录 / 注册入口**（`/login` 改为「登录已关闭」提示页、审计用户身份留空）。仅用于本地调试，**生产务必保持 `true`**。前端经 `GET /api/meta` 的 `auth_enabled` 字段感知当前模式。

### 9.3 凭据安全（传输与存储）

1. 密码经 **bcrypt** 加盐哈希存储，库中绝不存明文；
2. 审计日志写入前对请求 / 响应体中的 `password` / `token` 等敏感字段**自动脱敏**（遮蔽为 `***`），登录 / 注册接口的明文密码不会进入审计文件或审计库；
3. 会话 Cookie 为 **HttpOnly + SameSite=Lax**，生产（`GOTY_SESSION_COOKIE_SECURE=true`）下带 **Secure** 标记、仅经 HTTPS 下发，并同时下发 **HSTS**（`Strict-Transport-Security`）；
4. 登录 / 注册响应**不回显密码**。

> **部署硬性要求：账号体系依赖 HTTPS（TLS）承载**——凭据明文仅在 TLS 加密通道内传输。生产请将反向代理 / 网关配置为 TLS 终止，并设 `GOTY_SESSION_COOKIE_SECURE=true`。本地 http 开发（`false`）仅用于调试，切勿暴露到公网。

### 9.4 注册字段校验（前后端一致）

内置登录页（`GET /login`）在前端做字段级中文错误提示；服务端再次校验，错误码统一映射为中文（如 `username_taken` →「该用户名已被注册」）。规则：

- 用户名 `^[A-Za-z0-9_.-]{3,32}$`，**不允许重名**（`409 username_taken`）；
- 邮箱为可选但**若填写必须格式正确**（`invalid_email`）；
- 密码**至少 8 位且同时含字母与数字**（`weak_password`）。

前端预校验拦截后不发请求；后端为最终权威。

> 运维建号（注册关闭时）：用 `SyncUserStore` 脚本直接 `register(username, password)` 写库；或临时设 `GOTY_AUTH_REGISTRATION_OPEN=true` 经 `/api/auth/register` 自助注册后再关回。

### 9.5 认证相关环境变量

| 环境变量 | 含义 | 默认 |
|----------|------|------|
| `GOTY_AUTH_ENABLED` | 是否开启账号体系；`false` = 「全部免登录」本地调试模式 | `true` |
| `GOTY_AUTH_REGISTRATION_OPEN` | 是否开放自助注册；`false` 时注册接口整体 `403` | `true` |
| `GOTY_EXPLORE_REQUIRES_AUTH` | 探索页是否需要登录方可进入 | `true` |
| `GOTY_USERS_DB_URL` | 用户库 SQLAlchemy URL（换 MySQL / PostgreSQL 仅改此值） | `sqlite:///./data/users.db` |
| `GOTY_USERS_DB_ECHO` | 打印用户库 SQL（调试用） | `false` |
| `GOTY_SESSION_TTL_SECONDS` | 会话有效期（秒） | `604800` |
| `GOTY_SESSION_COOKIE_NAME` | 会话 Cookie 名（HttpOnly + SameSite=Lax） | `goty_session` |
| `GOTY_SESSION_COOKIE_SECURE` | Cookie 仅经 HTTPS 下发（生产 https 部署设 `true`；本地 http 开发保持 `false`） | `false` |

> 两套存储（`api/auth/store.py` 用户库 vs `api/audit/store.py` 审计库）**独立建库**，请勿共用同一文件。
