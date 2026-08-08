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

    # ---- 访问控制：UA 策略（拦截明显爬虫，仅放行真实浏览器）----
    # 默认关闭：开启后凡 UA 命中 bot_ua_blocklist 的请求直接 403；避免误伤合法非浏览器
    # 客户端与测试（TestClient 默认 UA 即 python-httpx）。需要「只放行浏览器」时设为 true。
    block_bot_ua: bool = False
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
