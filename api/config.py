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


@lru_cache
def get_settings() -> Settings:
    """进程级单例；测试可通过 ``Settings(...)`` 自行构造后传入 ``create_app``。"""
    return Settings()
