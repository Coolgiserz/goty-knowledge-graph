"""安全上下文：把 :class:`api.config.Settings` 组装成可直接使用的安全原语集合。

以前这些阈值散落在 ``ratelimit.SecurityConfig`` 里直接读环境变量；现在统一由
pydantic-settings 解析后在此组装，便于测试注入、也避免重复读环境。应用工厂
(:func:`api.app.create_app`) 负责构造并在 ``app.state.security`` 上挂载，
中间件与依赖通过 ``request.app.state`` 取用。
"""

from .config import Settings
from .ratelimit import Blacklist, RateLimiter, create_rate_limiter, get_client_ip


class SecurityContext:
    """一次装配、全局复用的安全原语。"""

    def __init__(self, settings: Settings):
        self.trust_proxy = settings.trust_proxy
        # 限流后端经工厂选择（默认内存 Limiter；配 GOTY_RATE_LIMIT_REDIS_URL 可换 Redis），
        # 类型统一为 RateLimiter 协议，调用方无感知。
        self.general_limiter: RateLimiter = create_rate_limiter(
            settings.rate_limit_max, settings.rate_window, redis_url=settings.rate_limit_redis_url
        )
        self.board_limiter: RateLimiter = create_rate_limiter(
            settings.board_limit_max, settings.board_window, redis_url=settings.rate_limit_redis_url
        )
        seed = (
            [ip.strip() for ip in settings.blacklist.split(",") if ip.strip()]
            if settings.blacklist
            else []
        )
        self.blacklist = Blacklist(seed=seed, file_path=settings.blacklist_file)
        self.autoban_violations = settings.autoban_violations
        self.autoban_seconds = settings.autoban_seconds

    def client_ip(self, request) -> str:
        return get_client_ip(request, self.trust_proxy)
