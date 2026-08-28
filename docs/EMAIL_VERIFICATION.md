# 邮件验证功能设计 · GOTY 知识图谱

> **状态：已实现（v1.8.5）。** 本文档描述在现有认证分层（`api/auth`）上扩展「邮箱验证」的方案，原设计提案已落地为代码（见第 11 节改动清单对应的 `api/auth` 各模块）。
>
> 本文在「设计 + 原理 + 可行性」三个层面展开，并在第 13 节汇总了一轮设计评审问答（MailHog 本地 SMTP、令牌是否存 Redis、依赖锁定文件定位、邮件 I/O 是否用后台任务）。

---

## 1. 目标与已确认的方向

- **注册邮箱改为必填**：开启验证后，自助注册必须提供合法邮箱，否则返回 `400 invalid_email`。
- **硬策略（验证前禁止登录）**：未验证邮箱的账号无法登录（返回 `401 email_not_verified`）；验证完成后方可正常登录。
- **贴合现有分层**：业务规则只落在 `api/auth/service.py`；`store` 负责持久化、`router` 加接口与 DTO、`pages` 加确认/重发 UI、`config` 加开关，遵循既定「扩展只动 service」的架构约束。
- **本地 / demo 零依赖可用**：邮件发送抽象支持「控制台/日志」模式，无需 SMTP 即可联调。

---

## 2. 设计原理（为什么这样设计）

### 2.1 为什么用「令牌式」邮箱验证

邮箱验证的本质是证明「注册者能收到该邮箱的邮件」。业界通用做法：服务端生成一段**不可猜解的随机令牌**，通过邮件发给用户；用户点击含令牌的链接回来，服务端校验令牌后即认定邮箱归该用户所有。

- **不放 JWT / 不把状态编码进令牌**：令牌本身无业务含义，只作为「数据库里某条待验证记录的指针」。这样令牌可被吊销、可设过期、可审计「谁在何时验证了什么」，比自包含 JWT 更可控。
- **令牌存于「单一真相源」**：与本项目「会话存库」（`SessionRow`）思路一致——状态以存储为唯一真相源，进程重启 / 多副本不丢（具体到存 DB 还是 Redis，见 2.7）。

### 2.2 单次有效 + 短 TTL

- **单次有效**：验证成功后立即销毁令牌，避免同一链接被 replay（用户转发邮件、或令牌意外泄露后被二次使用）。
- **短 TTL（默认 1 小时）**：缩短令牌在泄露窗口内的有效期。邮箱验证属低敏操作（只影响「能否登录」），1 小时足够完成点击，又不会因长期有效放大风险。
- 两者共同把「令牌泄露」的实际危害压到最低：即使链接出现在浏览器历史 / 代理日志里，过期或已被消费后即失效。

### 2.3 硬策略，但「默认安全」的迁移

硬策略（未验证禁止登录）更严格，但有一个现实风险：**存量账号在加列前就已存在，若默认 `email_verified=False`，老用户会被一刀切锁死**。

化解方式：新增 `users.email_verified` 列后，立即 `UPDATE users SET email_verified=1` 把**所有既有行标记为已验证**（受信任的历史账号），只有**此后新注册**的账号才以 `False` 起步。两个开关默认值都为 `true`，但运维可随时设 `false` 退回旧行为——「新功能默认开启，却不破坏存量、可一键降级」，正是本项目在 UA 拦截、探索开关等处反复采用的兜底哲学。

### 2.4 可插拔邮件发送器（含 console 模式）

本项目**当前没有任何 SMTP 配置**，若强制要求真实邮件发送，本地开发与 demo 将无法联调。因此抽一个 `MailSender` 协议：

- `ConsoleMailSender`：把验证链接打印到应用日志（必要时在 `off/console` 模式下让接口把令牌一并回显），**零依赖、零外部服务**，和本项目「免登录调试开关」的本地优先取向一脉相承。
- `SmtpMailSender`：用 Python 标准库 `smtplib` + `email.message.EmailMessage` 发送。**不引入任何第三方依赖**（规避 v1.8.1 漏导 `requirements.lock.txt` 导致容器缺包的覆辙）。
- 本地测试推荐用 **MailHog** 作为 SMTP（见 13.1）：它接受无鉴权 SMTP、并提供 Web UI 检视捕获的邮件，是最贴近真实的联调方式，且无需真实邮箱账号。

由 `GOTY_MAIL_MODE` 在 `off / console / smtp` 间切换，未来接 SendGrid / SES 也只需再加一个实现，路由层零改动。

### 2.5 防账号枚举

- **重发接口恒定成功**：无论邮箱是否存在、是否已验证，`POST /api/auth/request-verification` 都返回 `200`。
- **验证接口按令牌判定，不按邮箱**：`POST /api/auth/verify-email` 只认令牌；令牌不可猜解，故无法借此探测账号。

> 注：注册接口本就对重名返回 `409 username_taken`（已有枚举面），本次不扩大该面，只在新增接口上严格收敛。

### 2.6 与现有分层架构对齐

现有 `api/auth` 已严格分层：路由 → 服务 → 存储 → 页面。邮件验证**不打破**这一约束：

- 业务规则（是否必填、是否拦截未验证登录、令牌生成与消费）全部在 `service` 层；
- 存储层只负责把「是否已验证」与「令牌」持久化；
- 令牌生成用已有的 `secrets.token_urlsafe` 思路，与 `SessionRow.id` 同源；
- 迁移沿用审计库 `_NEW_AUDIT_COLUMNS` / `_migrate_audit_columns_sync` 的 ALTER 套路（仅针对 `email_verified` 这一持久列）。

后续若要做密码重置、OAuth、登录限额，仍只在 `service` 层扩展，本提案不制造例外。

### 2.7 令牌存储：可插拔（DB 表 / Redis），而非写死在 `User` 上

令牌是**临时、单次、短命**的东西——它不该以三列 NULL-able 字段挂在 `User` 表上（否则 `users` 表长期躺着大量空列，且过期清理要额外扫表）。更合理的做法是把令牌放进一个**独立的、可替换的 `TokenStore`**：

- **DB 后端（默认，零新依赖）**：独立的 `email_tokens` 表（`token` PK、`user_id`、`type`、`expires_at`、`created_at`）。它是**新表**，不需要对 `users` 做 ALTER；消费时校验 `expires_at` 并删除行，过期未消费的行可周期清理（或仅在使用时判定失效）。
- **Redis 后端（可选，当配置了 Redis 时）**：直接 `SET token <user_id> EX <ttl>`——**利用 Redis 原生 TTL 自动过期**，无需任何过期扫表、无需 `expires_at` 字段、天然契合「临时有意义」的令牌语义。本项目速率限制已支持 `GOTY_RATE_LIMIT_REDIS_URL`，令牌存储可**复用同一个 Redis URL**（键加前缀区分），不引入新的基础设施。

选择逻辑（部署驱动，非代码驱动）：`GOTY_RATE_LIMIT_REDIS_URL` 非空 → 用 Redis 后端（推荐，令牌语义最贴合）；否则 → 用 DB 表后端。这样**既承认「Redis 才是 ephemeral token 的更优归宿」这一批评，又不强制项目引入 Redis 硬依赖**（保持零依赖本地可跑）。`email_verified` 这种**持久**状态仍留在 `User` 上（它不属于临时数据）。

---

## 3. 配置开关（`api/config.py`）

所有开关收敛进 `Settings`（`env_prefix="GOTY_"`），测试可经 `Settings(...)` 直接注入。

| 环境变量 | 字段 | 默认 | 含义 |
|----------|------|------|------|
| `GOTY_AUTH_EMAIL_REQUIRED` | `auth_email_required` | `true` | 自助注册是否强制填写邮箱 |
| `GOTY_AUTH_REQUIRE_EMAIL_VERIFIED` | `auth_require_email_verified` | `true` | 未验证邮箱是否禁止登录 |
| `GOTY_MAIL_MODE` | `mail_mode` | `console` | 邮件发送方式：`off` / `console` / `smtp` |
| `GOTY_APP_PUBLIC_URL` | `app_public_url` | `""` | 验证链接的公网基址（避免容器内 localhost） |
| `GOTY_MAIL_FROM` | `mail_from` | `""` | 发件人地址 |
| `GOTY_SMTP_HOST` / `GOTY_SMTP_PORT` / `GOTY_SMTP_USER` / `GOTY_SMTP_PASSWORD` | `smtp_*` | `localhost` / `1025` / 空 / 空 | SMTP 连接信息（`mail_mode=smtp` 时使用；本地默认指向 MailHog） |
| `GOTY_EMAIL_VERIFY_TTL_SECONDS` | `email_verify_ttl_seconds` | `3600` | 验证令牌有效期（秒） |
| `GOTY_RATE_LIMIT_REDIS_URL` | `rate_limit_redis_url` | `""` | 若非空，令牌存储也走 Redis（复用速率限制的同一条 Redis） |

> 默认值取向：新功能默认开启，但通过开关与迁移保证**不破坏存量、可一键降级**。

---

## 4. 数据模型与迁移

### 4.1 持久列（在 `User` 上加一列）

```python
class User(AuthBase):
    __tablename__ = "users"
    # ... 现有列 ...
    email_verified: Mapped[bool] = mapped_column(default=False)   # 持久状态，非临时
```

- 这是**持久**标志，走 ALTER 迁移（复用审计库 `_NEW_AUDIT_COLUMNS` / `_migrate_audit_columns_sync` 套路：inspect 探列 → `ALTER TABLE users ADD COLUMN email_verified BOOLEAN NOT NULL DEFAULT 0` → `UPDATE users SET email_verified=1` 标存量账号为已验证）。`server_default` 保证旧行写入不受影响。

### 4.2 临时令牌（独立 `email_tokens` 表 / 或 Redis）

**DB 后端**——新建表（无需对 `users` ALTER，随 `metadata.create_all` 自动建）：

```python
class EmailToken(AuthBase):
    __tablename__ = "email_tokens"
    token: Mapped[str] = mapped_column(String(43), primary_key=True)  # secrets.token_urlsafe(32)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(16), default="verify")   # verify / reset（预留）
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

**Redis 后端**——无表，键 `goty:verify:{token}` → `user_id`，`EX = ttl`。

### 4.3 `TokenStore` 抽象（两种后端共用接口）

```python
class TokenStore(Protocol):
    async def create(self, user_id: int, token: str, ttl: int) -> None: ...
    async def consume(self, token: str) -> int | None:
        """原子：取 user_id + 校验未过期 + 删除；不存在/过期返回 None"""
        ...
    async def clear_for_user(self, user_id: int) -> None: ...
```

- DB 实现：`create` 插入 `email_tokens`；`consume` `SELECT ... WHERE token=? AND expires_at>now()`，命中则删行并返回 `user_id`，否则 `None`（过期行顺手清理）；`clear_for_user` 删该用户全部令牌（重发覆盖）。
- Redis 实现：`create` → `SET goty:verify:{token} {user_id} EX ttl`；`consume` → `GET` + `DEL`（Lua 保证原子）；`clear_for_user` 需按 `user_id` 反查，故 Redis 后端额外维护 `goty:verify:byuser:{user_id}` 集合，或简单起见重发直接覆盖旧 token 值（旧 token 自然过期）。
- 工厂 `create_token_store(settings, user_store_url)`：Redis URL 非空返回 Redis 实现，否则返回 DB 实现。

---

## 5. 邮件发送器（`api/auth/mail.py`，新增）

```python
class MailSender(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...

class ConsoleMailSender:
    def send(self, to, subject, body):
        logger.info("【验证码邮件(console)】to=%s\nsubject=%s\n%s", to, subject, body)

class SmtpMailSender:
    def __init__(self, host, port, user, password, sender): ...
    def send(self, to, subject, body):
        from email.message import EmailMessage
        from smtplib import SMTP
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        with SMTP(self.host, self.port) as smtp:        # 标准库，无新依赖
            if self.user: smtp.login(self.user, self.password)
            smtp.send_message(msg)
```

`create_mail_sender(settings)` 按 `mail_mode` 返回对应实现；`off` 模式下 `send` 直接 no-op（纯本地调试，令牌改由接口回显）。**本地测试用 MailHog** 时设 `mail_mode=smtp` + `smtp_host=localhost` + `smtp_port=1025`（无 user/password、无 TLS），捕获的邮件在 http://localhost:8025 查看（见 13.1）。

---

## 6. 服务层接口（`api/auth/service.py`）

新增错误类（与 `/login` 页中文映射一一对应）：

```python
class EmailRequired(AuthError):       # 400 email_required    注册未填邮箱
    ...
class EmailNotVerified(AuthError):    # 401 email_not_verified 未验证禁止登录
    ...
# invalid_or_expired_token(400) / already_verified(409) 复用 AuthError 直接构造
```

核心函数（伪代码）：

```python
async def request_email_verification(store, token_store, user, mail_sender, settings):
    token = secrets.token_urlsafe(32)
    await token_store.create(user.id, token, settings.email_verify_ttl_seconds)  # 覆盖旧令牌
    link = f"{settings.app_public_url or ''}/verify-email?token={token}"
    mail_sender.send(user.email, "请验证你的邮箱", f"点击完成验证：{link}")
    # 无论成功与否均返回；枚举防护在路由层恒定 200

async def verify_email(store, token_store, token) -> User:
    user_id = await token_store.consume(token)     # 原子：校验+单次消费
    if user_id is None:
        raise AuthError(400, "invalid_or_expired_token")
    user = await store.get_user(user_id)
    if user is None:
        raise AuthError(400, "invalid_or_expired_token")
    if user.email_verified:
        raise AuthError(409, "already_verified")
    await store.mark_verified(user.id)             # 置 True（User 持久列）
    return user
```

`register_user` / `authenticate` 调整：

- `register_user`：当 `auth_email_required` 且 `email` 为空 → `EmailRequired`；现有 `EMAIL_RE` 非法邮箱校验保留；新建用户 `email_verified=False`。
- `authenticate`：当 `auth_require_email_verified` 且 `user.email_verified is False` → `EmailNotVerified`（现有凭据校验逻辑不变）。

---

## 7. 路由与页面（`api/routers/auth.py` + `api/auth/pages.py`）

### 7.1 新增接口

- `POST /api/auth/request-verification`：请求体 `{email}`（或凭当前登录态）；**带频控**（复用既有限流原语 / 单用户时间窗），始终返回 `200`。
- `POST /api/auth/verify-email`：请求体 `{token}`，调 `service.verify_email`。
- `GET /verify-email?token=...`（页面，放 `pages.py`）：薄确认页，打开即调上面的接口，渲染「验证成功 / 链接无效或已过期」并提供「去登录」入口。

### 7.2 寄存器变更（硬策略下的注册流程）

当 `auth_require_email_verified` 开启，**注册接口不再自动登录**（不发会话 Cookie），返回用户 + 提示「请查收验证邮件后登录」。软策略（开关关）维持现状（注册即自动登录，验证仅记录）。

### 7.3 登录页（`pages.py` 的 `/login`）

增加「重发验证邮件」入口；登录失败若因 `email_not_verified`，给出明确中文提示「请先验证邮箱」。

### 7.4 错误码中文映射（新增）

| 错误码 | HTTP | 中文（登录页映射） |
|--------|------|--------------------|
| `email_required` | 400 | 请填写邮箱 |
| `invalid_email` | 400 | 邮箱格式不正确（既有） |
| `email_not_verified` | 401 | 请先验证邮箱后再登录 |
| `invalid_or_expired_token` | 400 | 验证链接无效或已过期 |
| `already_verified` | 409 | 该邮箱已验证 |

### 7.5 路由豁免

`/api/auth/*` 与 `/verify-email` 页面均在探索守卫（`/explore` 307 跳 `/login`）与登录门禁的豁免清单内（与现有 `/login` 处理一致），避免「未登录 → 跳登录 → 又要验证 → 死循环」。

---

## 8. 流程时序

### 8.1 硬策略下注册 → 验证 → 登录

```
用户          前端/SPA        POST /api/auth/register   service.register_user   store   token_store
 |               |                     |                        |                  |        |
 |--填表提交---->|                     |                        |                  |        |
 |               |-- username/pwd/email-->|                        |                  |        |
 |               |                     |--校验(必填/格式/重名)-->|                  |        |
 |               |                     |-- 建号(email_verified=F)------------------>|        |
 |               |<-- 200 用户(不发会话,提示查收) ----------|                  |        |
 |               |                                                                      |
 |   (收到邮件)  |                                                                      |
 |--点击链接---->|  GET /verify-email?token=xxx  (pages 渲染)                             |
 |               |-- POST /api/auth/verify-email {token} --> service.verify_email ----->|
 |               |                     |-- token_store.consume(校验+删)---->|          |
 |               |                     |-- store.mark_verified(True)---------->|          |
 |               |<-- 200 验证成功，去登录 --------------------------|                  |        |
 |--登录-------->|  POST /api/auth/login --> service.authenticate                 |
 |               |                     |-- email_verified=True 通过 --> 发会话 -->  |
```

### 8.2 重发 / 过期

- 令牌过期或误删链接 → 用户在登录页点「重发验证邮件」→ `request-verification` 重新生成令牌并发送（旧令牌被覆盖/失效）。
- 同一令牌消费后再次使用 → `invalid_or_expired_token`。

---

## 9. 安全考量

- **防枚举**：重发接口与验证接口都不泄露邮箱 / 账号是否存在（重发恒成功；验证按 token 判定）。
- **令牌**：32 字节 URL-safe 随机值，单次有效、设 TTL，消费后立即销毁（Redis 后端更是由 TTL 自动回收）。
- **敏感信息**：令牌仅出现在验证链接 / 控制台日志（dev），**绝不写入审计落盘**——沿用 `api/middleware.py` 的 `_redact_sensitive`，其已遮蔽 `password` / `token` / `secret` / `authorization` 等键，验证令牌不会进审计文件或审计库。
- **频控**：重发接口限频，避免被用来轰炸任意邮箱或探测账号。
- **HTTPS**：验证链接经公网访问，部署须 TLS（`GOTY_SESSION_COOKIE_SECURE=true`），与现有凭据安全策略一致；令牌出现在 URL 中，短 TTL + 单次有效 + HTTPS 将其泄露危害降至最低。

---

## 10. 可行性分析（能否落地、风险与化解）

### 10.1 依赖与零成本本地联调

- **真实邮件走标准库 `smtplib`**，不新增任何第三方包 → `pyproject.toml` / `uv.lock` / `requirements.lock.txt` **完全不动**，从源头规避 v1.8.1「漏导依赖致容器缺包」的坑。
- **`console` 模式零外部服务**：本地开发与 demo 直接打印链接即可完成验证闭环，无需配置 SMTP。
- **`mail_mode=smtp` + MailHog** 是最贴近真实的本地联调路径（见 13.1），无需真实邮箱账号、自带 Web 检视。
- 结论：**本地 5 分钟内即可跑通全流程**，生产再切真实 SMTP。

> **关于 `requirements.lock.txt` 的定位（附）**：它是 `uv export --no-dev` 生成的 **pip 风格派生锁文件**，仅被 Dockerfile 的 `uv pip install --system -r requirements.lock.txt` 使用；`uv.lock` 才是 `uv sync`（本地/CI）的真相源。它**有价值但属于派生产物**，v1.8.1 的 bcrypt 缺包正是「改 `pyproject` 后忘了重导它」所致。建议：①**主依赖管理以 `pyproject.toml` + `uv.lock` 为唯一真相源**，本功能零新依赖故不触碰它；②不要让 `requirements.lock.txt` 成为漂移源——要么在 Makefile 加 `make lock`（`uv export --no-dev -o requirements.lock.txt`）+ CI 校验其是否与 `uv.lock` 同步，要么**更彻底地让 Dockerfile 改用 `uv sync --no-dev --frozen`**（直接消费 `uv.lock`，删掉 `requirements.lock.txt`，从根上消除双锁文件漂移）。参见 13.3。

### 10.2 数据库迁移安全性

- `email_verified` 列的 `ALTER TABLE ... ADD COLUMN` 在 SQLite / PostgreSQL / MySQL 均为在线安全操作；新增列带 `server_default`，不影响旧行写入；迁移复用审计库成熟的套路（`inspect` 探列 + 失败静默放过）。
- 存量 `UPDATE ... SET email_verified=1` 幂等，确保老账号不被硬策略锁死。
- 临时令牌走**独立新表 `email_tokens`**（随 `metadata.create_all` 自动建，**不需要对 `users` ALTER**），或走 Redis（无表）；避免「在 `users` 上堆三列 NULL」的尴尬。

### 10.3 性能：邮件 I/O 不阻塞事件循环

`smtp` 发送是**同步阻塞 I/O**（建连 + 发信可能数百毫秒）。两条关键事实：

- **FastAPI `BackgroundTasks` 跑在同一事件循环上**，只是「响应返回后再执行」——它**不会**自动把阻塞调用挪出事件循环。若后台任务里直接同步调 `smtplib`，**仍会阻塞事件循环**（只是延后到响应之后）。因此 `BackgroundTasks` 单独不足以解决阻塞 I/O，它只改善「用户感知延迟」（先拿到响应）。
- 正确做法：**`BackgroundTasks` + `await asyncio.to_thread(mail_sender.send, ...)`**——既先返回响应（好体验），又把阻塞的 SMTP 调用丢进线程池（不占事件循环）。纯 `asyncio.to_thread(...)` 内联也可行（请求会等发信完成，低流量可接受），但配合 `BackgroundTasks` 体验更佳。

```python
# 路由内
background_tasks.add_task(_send_verify_email, mail_sender, user.email, link)
# _send_verify_email 内部：
await asyncio.to_thread(mail_sender.send, to, subject, body)
```

`console` 模式仅为日志写入，`to_thread` 开销可忽略，但为路径统一仍走线程池。多实例下 `to_thread` 由 asyncio 默认线程池承载，安全。

### 10.4 与现有机制的交互

| 机制 | 交互结论 |
|------|----------|
| `GOTY_AUTH_ENABLED=false`（免登录） | 整个认证体系禁用，邮件验证自然**完全惰性**，不触发 |
| 审计脱敏 `_redact_sensitive` | 验证令牌在请求体里被自动遮蔽，不落盘 |
| UA 拦截 `GOTY_BLOCK_BOT_UA` | `/api/auth/*` 与 `/verify-email` 页面本就在豁免清单，邮件里的真实浏览器点击不受影响 |
| 探索守卫 `/explore` 307 | `/verify-email` 是根路径页面、非 `/explore`，不经守卫；验证接口在 `/api/auth` 下，豁免登录守卫 |
| 会话/Cookie | 硬策略下注册不自动登录；验证成功后再走正常 `/login` 流程，会话机制完全复用 |

### 10.5 多实例与并发

- **DB 后端**：令牌表在共享数据库（MySQL / PostgreSQL）时跨副本一致；SQLite 单实例仅适用 demo。
- **Redis 后端**：令牌天然在共享 Redis，跨副本一致，且 TTL 自动回收，是水平扩展下的更优选择（本项目速率限制已用同一条 Redis）。
- `consume` 为「查 + 删」原子操作（DB 用事务 / Redis 用 Lua），避免竞态下双重消费；重发「覆盖旧令牌」是单用户粒度短时事务，并发安全。

### 10.6 风险与缓解

| 风险 | 缓解 |
|------|------|
| SMTP 凭证泄露 | 仅存于 `.env` / 环境变量（与 `admin_token` 同策略），绝不入库或硬编码 |
| 邮件进垃圾箱 / 送达失败 | `mail_from` 用可信域名；demo 用 `console` / MailHog 规避；提供「重发」兜底 |
| 令牌随 URL 泄露（历史/代理日志） | 短 TTL + 单次有效 + HTTPS；Redis 后端更由 TTL 自动回收 |
| 重发接口被滥用轰炸第三方邮箱 | 单用户 + 全局频控；恒定 200 不泄露是否存在 |
| 存量账号被硬策略锁死 | 迁移 `UPDATE email_verified=1` + 开关可降级 |

### 10.7 测试兼容性

- `Settings` 新增字段均有默认值，现有 `Settings()` / `Settings(...)` 构造不受影响；既有 `tests/test_auth.py` 用例继续有效。
- **需同步更新的点**：`RegisterRequest.email` 在 `auth_email_required` 默认开启下变为必填，构造该请求的用例须补 `email` 字段；新增断言：① 空邮箱注册 `400 email_required`；② 未验证登录 `401 email_not_verified`；③ 验证成功后可登录；④ 过期/复用令牌 `400`；⑤ 重发接口恒定 `200`；⑥ CLI（`SyncUserStore`）建的账号默认 `email_verified=True`、可直接登录；⑦ TokenStore 双后端（DB / Redis）消费幂等。
- 现有 `make ci`（ruff + pytest + perf）门禁不受影响；新增用例放在 `tests/test_auth.py`。

---

## 11. 分层改动清单（已实现对照）

| 层 | 文件 | 改动 |
|----|------|------|
| 存储 | `api/auth/models.py` | `User` 加 `email_verified`（持久列）；新增 `EmailToken` 模型（DB 后端用） |
| 存储 | `api/auth/store.py` | `email_verified` ALTER 迁移 + 存量标记；`UserStore` / `SyncUserStore` 新增 `mark_verified` / `get_user`(已有)；新增 `TokenStore` 抽象 + DB / Redis 两实现 + 工厂 |
| 业务 | `api/auth/service.py` | `register_user` / `authenticate` 调整 + 新增 `request_email_verification` / `verify_email` + 错误类 |
| 邮件 | `api/auth/mail.py`（新） | `MailSender` 协议 + `ConsoleMailSender` / `SmtpMailSender` + `create_mail_sender` 工厂 |
| 路由 | `api/routers/auth.py` | 新增 2 接口 + DTO（含 `BackgroundTasks` 发信）；注册在硬策略下不自动登录 |
| 页面 | `api/auth/pages.py` | 验证确认页 + 登录页重发入口 |
| 配置 | `api/config.py` | 本节第 3 节全部开关 |
| 测试 | `tests/test_auth.py` | 必填邮箱、未验证禁登录、令牌消费/过期、重发幂等、CLI 账号默认可登录、TokenStore 双后端 |
| 集成测试 | `tests/integration/test_email_integration.py` | 真实走 SMTP 发信路径（进程内 `socketserver` SMTP 捕获，零依赖）；`make test-integration` 触发，默认不进 `make test`/`make ci` |

---

## 12. 开放问题与后续

- **密码重置**：复用 `email_tokens` 表的 `type='reset'`（或 Redis 同构键），与验证共享 `TokenStore` 发放/消费逻辑——这正是把令牌从 `User` 抽成独立存储的演进收益。
- **换邮箱**：验证新邮箱前保留旧邮箱，验证成功后覆盖；本期暂不实现。
- **多实例邮件发送**：`smtp` 模式下 `BackgroundTasks` + `to_thread` 已足够；若未来量级上升，可改为复用现有 `TaskManager` 线程池。
- **OAuth / 登录限额**：按既定架构，仍只在 `service` 层扩展。

---

## 13. 设计评审问答（FAQ）

### 13.1 本地测试用 MailHog 作为 SMTP

完全采纳。MailHog 是理想的本地/测试 SMTP：接受**无鉴权** SMTP、并提供 Web UI 检视捕获的邮件，无需真实邮箱账号。

- 启动：`docker run -d -p 1025:1025 -p 8025:8025 mailhog/mailhog`
- 配置（`.env`）：`GOTY_MAIL_MODE=smtp`、`GOTY_SMTP_HOST=localhost`、`GOTY_SMTP_PORT=1025`、`GOTY_SMTP_USER=`(空)、`GOTY_SMTP_PASSWORD=`(空)（无需 TLS/登录）。
- 检视：`http://localhost:8025`（点邮件里的验证链接即可完整走通）。
- 与 `console` 模式的关系：`console` 用于「零依赖、连 MailHog 都不想起」的最轻联调；`smtp`+MailHog 用于「尽量贴近生产路径」的联调。两者都无需改动代码。

### 13.2 令牌为什么不存 Redis 而是存数据库？它只是临时有意义的东西

这是合理批评，已据此重构（见 2.7 / 4.2 / 11）：

- **认同**：验证令牌是临时、单次、短命数据——它确实更适合 Redis 的「原生 TTL 自动过期」，而不是关系表里三列 NULL 字段（还要扫表清理过期）。
- **但**项目默认是**零依赖、本地 `make serve` 即可跑**的；若把令牌**强制**存 Redis，就等于给所有部署强加一个 Redis 依赖，违背这一取向（速率限制的 Redis 本是「可选」）。
- **最终方案**：令牌存储做成**可插拔 `TokenStore`**——配置了 Redis（`GOTY_RATE_LIMIT_REDIS_URL`，速率限制已用）就走 Redis（推荐，语义最贴合）；否则走**独立 `email_tokens` 表**（新表、不污染 `users`）。选择由部署决定，不由代码写死。这样既承认「Redis 是 ephemeral token 的更优归宿」，又不破坏零依赖本地体验。

### 13.3 `requirements.lock.txt` 有什么价值？是否建议以此管理依赖？

- **它的价值**：是 `uv export --no-dev` 生成的 **pip 风格派生锁文件**，仅被 Dockerfile 的 `uv pip install --system -r requirements.lock.txt` 消费；`uv.lock` 才是 `uv sync`（本地/CI）的真相源。它在「容器用 `uv pip install` 而非 `uv sync`」的路径下有用，且是**完全解析、不含 dev 组**的。
- **它的代价**：属于派生产物，必须手动 `uv export` 重新生成——v1.8.1 的 bcrypt 缺包正是忘了重导它。现在 Makefile 里**没有 `make lock` 目标**来兜底。
- **我的建议**：
  1. **以 `pyproject.toml` + `uv.lock` 为唯一真相源**管理依赖（uv 已是本项目标准）。
  2. **不要让 `requirements.lock.txt` 成为漂移源**：二选一——
     - （推荐）**删掉它**：Dockerfile 改用 `uv sync --no-dev --frozen`（直接消费 `uv.lock`，`--frozen` 保证 CI 用提交锁），从根上消除双锁文件不一致；或
     - （保留路径）加 `make lock`（`uv export --no-dev -o requirements.lock.txt`）+ CI/pre-commit 校验 `requirements.lock.txt` 与 `uv.lock` 同步，防止再次忘导。
  3. 本邮件验证功能**零新依赖**（smtplib 标准库），因此不触碰 `uv.lock` / `requirements.lock.txt` 任何一份——这是刻意选择的最安全路径。

### 13.4 邮件 I/O 用 FastAPI 的后台任务是否更合适？

部分采纳，且要澄清一个常见误解（见 10.3）：

- **误解**：以为 `BackgroundTasks` 会自动把工作挪出事件循环。其实它只是「响应返回后再执行」，任务**仍在同一个事件循环上**；若里面直接同步调 `smtplib`，**照样阻塞事件循环**（只是延后到响应之后）。
- **合适用法**：`BackgroundTasks` 的价值是**先返回响应、改善用户感知延迟**；但它必须与 `asyncio.to_thread` 配合，把阻塞的 SMTP 调用丢进线程池，才真正不阻塞循环。即 **`BackgroundTasks.add_task` + 任务内部 `await asyncio.to_thread(mail_sender.send, ...)`**。
- 因此文档采用「`BackgroundTasks` + `to_thread`」组合，而非单纯 `BackgroundTasks` 或单纯内联 `to_thread`。
