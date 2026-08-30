"""集中配置：所有 ``GOTY_*`` 环境变量在此收敛为强类型 ``Settings``。

此前这些变量散落在 ``app.py`` / ``ratelimit.py`` / ``logging_config.py`` 各处直接读
``os.environ``；现统一由 pydantic-settings 解析，便于测试时注入、也避免拼写/类型不一致。

字段命名采用 ``env_prefix="GOTY_"``，因此：
    enable_exploration  -> GOTY_ENABLE_EXPLORATION
    explore_token       -> GOTY_EXPLORE_TOKEN
    task_workers        -> GOTY_TASK_WORKERS
    rate_limit_max      -> GOTY_RATE_LIMIT_MAX
    ... 其余同理
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GOTY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 探索总开关 / 任务参数（云端 demo 取向）----
    enable_exploration: bool = False
    explore_token: str = ""
    task_workers: int = 2
    max_pending: int = 5

    # ---- 安全防护：信任代理 / 两档限流 / 自动封禁 / 黑名单 ----
    trust_proxy: bool = True
    # 可信代理网段（逗号分隔的 IP / CIDR）。**仅当直连对端落在其中时**才采信
    # X-Forwarded-For / X-Real-IP——这两个头客户端可任意伪造。默认为空即不采信任何
    # 代理头，直接部署最安全；经反代/负载均衡时必须把反代地址填进来，否则所有请求
    # 都会被记成同一个反代 IP（限流会误伤整层）。
    trusted_proxies: str = ""
    rate_limit_max: int = 200
    rate_window: int = 60
    board_limit_max: int = 8
    board_window: int = 60
    autoban_violations: int = 5
    autoban_seconds: int = 3600
    blacklist_file: str = ""
    blacklist: str = ""  # 逗号分隔的永久封禁 IP 种子

    # ---- 图后端：networkx（默认，内存）| neo4j（可选，Cypher 查询）----
    # 选 neo4j 时需同时提供连接信息；连不上会自动回退到 networkx（见 api.graph_store）。
    graph_backend: str = "networkx"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # ---- 日志 ----
    log_level: str = "INFO"
    log_file: str = ""

    # ---- 请求审计日志 ----
    # 双写：① 时间轮转的审计日志文件（每行一条 JSON，便于 ELK/数仓采集）；
    #       ② 数据库（SQLAlchemy ORM，默认 sqlite 打通流程，未来换 mysql/OLAP 仅改 URL）。
    audit_enabled: bool = True
    audit_log_file: str = ""  # 审计日志文件路径（按时间周期轮转；留空=不写文件，仅入库）
    audit_rotate_when: str = "midnight"  # TimedRotatingFileHandler 的 when（midnight/hourly…）
    audit_rotate_backup: int = 14  # 保留备份份数
    audit_db_url: str = "sqlite:///./data/audit.db"  # SQLAlchemy URL；换库只改这里
    audit_db_echo: bool = False  # 打印 SQL（调试用）
    audit_body_max_bytes: int = 8192  # 请求体/响应体截断上限（字节）

    # ---- 请求源异常判定 ----
    anomaly_enabled: bool = True
    anomaly_frequency_max: int = 60  # 单 IP 在 anomaly_frequency_window 秒内最多请求数
    anomaly_frequency_window: int = 60  # 滑动窗口（秒），默认 1 分钟
    anomaly_ban_seconds: int = 86400  # 命中后封禁时长（秒），默认 24h

    # ---- 用户账号 / 会话（探索页登录门禁 + 审计记录用户身份）----
    # auth_enabled 默认开启：探索计算/提交接口与 /explore 页面要求登录。
    # 设为 false = 进入「全部免登录」本地调试模式：不要求任何登录、界面隐藏登录/注册入口
    # （/login 改为「登录已关闭」提示页），审计用户身份留空。仅用于本地调试，生产务必保持 true。
    auth_enabled: bool = True
    # 是否开放自助注册；关闭后只能通过预置管理员/脚本建账号（本期仅控制注册接口）。
    auth_registration_open: bool = True
    # 用户库（SQLAlchemy URL；默认 SQLite 文件，与审计库分离便于独立运维）。
    users_db_url: str = "sqlite:///./data/users.db"
    users_db_echo: bool = False
    # 会话时长（秒），默认 7 天；过期后需重新登录。
    session_ttl_seconds: int = 604800
    # 会话 Cookie 名称（HttpOnly + SameSite=Lax；生产建议加 Secure）。
    session_cookie_name: str = "goty_session"
    # 会话 Cookie 是否带 Secure（仅 HTTPS 下下发）。本地 http 开发设为 false，生产建议 true。
    session_cookie_secure: bool = False
    # 探索页（/explore）是否要求已登录；auth_enabled 关闭时此开关自动失效。
    explore_requires_auth: bool = True

    # ---- 邮箱验证（注册必填邮箱 + 验证前禁止登录）----
    # 两个开关默认开启（新功能默认开启，但通过迁移保证不破坏存量、可一键降级）。
    # auth_email_required：自助注册是否强制填写合法邮箱（空邮箱 -> 400 email_required）。
    # auth_require_email_verified：硬策略——未验证邮箱禁止登录（401 email_not_verified）。
    #   关闭则为软策略：注册即自动登录，邮箱验证仅作记录、不阻断登录。
    auth_email_required: bool = True
    auth_require_email_verified: bool = True
    # 验证令牌有效期（秒），默认 1 小时；短 TTL + 单次有效降低泄露危害。
    email_verify_ttl_seconds: int = 3600

    # ---- 邮件发送（零依赖：标准库 smtplib，不引入第三方包）----
    # mail_mode：off（纯本地调试，send 直接 no-op，令牌改由接口回显）/ console（仅打印链接到日志）/
    #            smtp（经标准库发真实邮件，本地可用 MailHog 指向 localhost:1025）。
    mail_mode: str = "console"
    # 验证链接的公网基址（容器内部 localhost 不可用时由运维指定，如 https://example.com）。
    app_public_url: str = ""
    # 发件人地址（留空时退化为 no-reply@goty.local，仅影响邮件头 From）。
    mail_from: str = ""
    # SMTP 连接信息（mail_mode=smtp 时使用）；本地默认指向 MailHog（无鉴权、无 TLS）。
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""

    # ---- 访问控制：UA 策略（拦截明显爬虫，仅放行真实浏览器）----
    # 默认开启：拦截疑似爬虫/脚本 UA（命中 bot_ua_blocklist 即 403），并拦截**空 UA**
    # （扫描器常见特征）。运维内部报表接口 ``/api/admin`` 前缀**豁免** UA 拦截，
    # 令牌守卫随后生效，避免 curl / 脚本访问被误伤。
    # 若你的调用方都是非浏览器脚本（服务间 API 调用），可设为 false 关闭。
    block_bot_ua: bool = True
    bot_ua_blocklist: str = (
        "python,java,go-http,golang,curl,wget,httpx,requests,scrapy,aiohttp,"
        "okhttp,guzzle,node,perl,ruby,php,bot,spider,crawl,slurp,headless,scraper,axios,urllib"
    )

    # ---- 限流后端可替换（未来接 Redis 等共享限流）----
    # 非空则走 RedisLimiter（需安装 redis），否则默认内存 Limiter。
    rate_limit_redis_url: str = ""

    # ---- 内部管理接口（站点访问统计报表）----
    # ``GET /api/admin/report`` 的访问令牌；留空 = 接口整体禁用（返回 403）。
    # 不下发到前端，仅供运维 / CLI 经 ``Authorization: Bearer <token>`` 或 ``?token=`` 调用。
    admin_token: str = ""


@lru_cache
def get_settings() -> Settings:
    """进程级单例；测试可通过 ``Settings(...)`` 自行构造后传入 ``create_app``。"""
    return Settings()
