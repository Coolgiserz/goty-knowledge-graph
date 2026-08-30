"""可复用的「安全 + 审计」中间件工厂。

把原先内联在 ``api.app`` 的 ``@app.middleware("http")`` 函数中间件抽成独立工厂，与具体
app 解耦：任何 ASGI app 都能通过 ``app.middleware("http")(create_security_audit_middleware(...))``
挂载。

设计要点（对齐 FastAPI async 最佳实践，不用 BaseHTTPMiddleware）：
- 横切顺序：**访问规则(如拦截爬虫UA) → 黑名单 → 限流 → 异常判定 → 处理 → 审计**。
- 依赖通过工厂参数注入（闭包捕获），运行时不再读取 ``app.state``，便于复用/测试。
- 审计 DB 写入为原生 async（``await audit_store.record_audit``），不占用线程、不阻塞事件循环，
  对齐 FastAPI 官方「有异步库就用 async def + await」的 async 最佳实践。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from .anomaly import AnomalyDetector
from .audit.store import AuditStore
from .auth.session import resolve_session_user
from .auth.store import UserStore
from .config import Settings
from .constants import ErrorCode
from .logging_config import log_audit_event
from .rules import AccessRule
from .security import SecurityContext
from .ua import derive_device

log = logging.getLogger("goty.api")

Middleware = Callable[
    [Request, Callable[[Request], Awaitable[Response]]],
    Awaitable[Response],
]


# 静态资源 Content-Type 前缀：命中即归为 asset，不计入审计/PV，避免噪声。
_ASSET_CT_PREFIXES = (
    "text/css",
    "application/javascript",
    "image/",
    "font/",
    "application/font",
    "application/octet-stream",
)

# 审计日志中需脱敏的请求/响应体字段（凭据 / 令牌等，绝不落明文）。
# 登录/注册接口的请求体含明文密码，必须遮蔽后再写审计文件与审计库。
_SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "current_password",
    "new_password",
    "token",
    "access_token",
    "refresh_token",
    "api_key",
    "apikey",
    "secret",
    "client_secret",
    "otp",
    "authorization",
}
_MASK = "***"
# 查询串（URL query）中需要遮蔽的参数名：管理报表与邮箱验证链接都用 ?token= 携带凭据。
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "password",
        "passwd",
        "pwd",
        "sig",
        "signature",
        "code",
        "session",
        "sid",
        "csrf_token",
    }
)
# 针对被截断（非完整 JSON）的请求体，回退正则遮蔽敏感键的值。
# 末组 ``("|$)`` 允许值缺少闭合引号——body 被截断在密码中间时也要能遮蔽，
# 否则会整段漏脱敏、把明文密码前缀写进审计。
_SENSITIVE_RE = re.compile(
    r'("(?:'
    + "|".join(re.escape(k) for k in sorted(_SENSITIVE_KEYS, key=len, reverse=True))
    + r')"\s*:\s*")([^"]*)("|$)',
    re.IGNORECASE,
)


def _mask_node(node) -> None:
    """就地把敏感键的值替换为 ``***``（递归 dict/list）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS:
                node[k] = _MASK
            else:
                _mask_node(v)
    elif isinstance(node, list):
        for item in node:
            _mask_node(item)


def _redact_sensitive(body: str) -> str:
    """对审计用的请求/响应体做凭据脱敏：仅遮蔽敏感字段的值，其余原样保留。

    - 完整个 JSON：解析后递归遮蔽敏感键，再序列化回去（结构/其余字段不变）。
    - 被截断的非完整 JSON：回退正则遮蔽 ``"key": "value"`` 形态的敏感键（正则**不要求**
      闭合引号，否则截断处正好落在密码值时就完全不脱敏、明文前缀直接落盘）。
    这样登录/注册接口的明文密码不会进入审计文件或审计库。

    调用方注意：**必须先脱敏、再截断**。顺序颠倒会先把 JSON 截成非完整串，
    再交给本函数的回退路径，脱敏可靠性大幅下降。
    """
    if not body:
        return body
    try:
        data = json.loads(body)
    except Exception:
        return _SENSITIVE_RE.sub(lambda m: m.group(1) + _MASK + m.group(3), body)
    _mask_node(data)
    try:
        return json.dumps(data, ensure_ascii=False)
    except Exception:
        # 序列化失败（理论极罕见）时绝不能退回未脱敏原文——宁可不记，也不能泄。
        return ""


def _redact_query(query: str) -> str:
    """脱敏查询串中的敏感参数值（``?token=xxx`` → ``?token=***``）。

    管理报表支持 ``?token=`` 携带管理令牌，邮箱验证落地页为 ``/verify-email?token=``，
    若不处理则凭据明文进入审计文件与审计库。只遮蔽值、保留参数名，便于排障。
    """
    if not query:
        return query
    out = []
    for part in query.split("&"):
        if not part:
            continue
        name, _sep, value = part.partition("=")
        if name.lower() in _SENSITIVE_QUERY_KEYS and value:
            out.append(f"{name}={_MASK}")
        else:
            out.append(part)
    return "&".join(out)


def _compute_visitor_id(ip: str, user_agent: str) -> str:
    """访客指纹：``sha256(ip|UA)[:16]``，不下发 Cookie，仅用于 UV 去重与活跃度统计。"""
    return hashlib.sha256(f"{ip}|{user_agent}".encode()).hexdigest()[:16]


def _classify_route(path: str, content_type: str) -> str:
    """按路径与响应 Content-Type 判定 ``route_type``。

    - ``/api/*`` 一律视为 ``api``（接口调用）。
    - 其余按 Content-Type：HTML/页面类为 ``page``（计入 PV），css/js/图片/字体等为 ``asset``
      （静态资源，不审计、不计 PV）。
    """
    ct = (content_type or "").lower()
    if path.startswith("/api/"):
        return "api"
    if any(ct.startswith(p) for p in _ASSET_CT_PREFIXES):
        return "asset"
    return "page"


def create_security_audit_middleware(
    security: SecurityContext,
    settings: Settings,
    audit_store: AuditStore | None,
    anomaly_detector: AnomalyDetector | None,
    access_rules: list[AccessRule] | None = None,
    user_store: UserStore | None = None,
) -> Middleware:
    """构造安全 + 审计中间件。

    参数均为依赖（闭包捕获），调用方负责传入已构造好的单例；中间件自身不读 ``app.state``，
    因此可被任意 app 复用。
    """
    rules = list(access_rules or [])

    async def middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        ip = security.client_ip(request)
        path = request.url.path
        method = request.method
        is_api = path.startswith("/api/")
        is_board = path.startswith("/api/board/") and method == "POST"
        audit_enabled = settings.audit_enabled
        anomaly_enabled = settings.anomaly_enabled

        response: Response | None = None
        anomaly_reasons: list[str] = []
        req_body = ""
        dur_ms = 0.0

        # 0) 访问规则（如拦截明显爬虫 UA）——即时 403
        for rule in rules:
            blocked, reason = rule.should_block(request)
            if blocked:
                log.warning("client=%s method=%s path=%s BLOCKED rule=%s", ip, method, path, reason)
                response = JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": ErrorCode.BLOCKED,
                        "message": "该请求来源被拒绝，请使用真实浏览器访问。",
                    },
                )
                break

        # 1) 黑名单
        if response is None:
            if security.blacklist.is_blacklisted(ip):
                log.warning("client=%s method=%s path=%s BLOCKED blacklisted", ip, method, path)
                response = JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={
                        "error": ErrorCode.BLACKLISTED,
                        "message": "您的访问已被限制，请联系管理员。",
                    },
                )
            else:
                # 2) 一般限流
                ok, retry = await security.general_limiter.check(ip)
                if not ok:
                    banned = security.blacklist.register_violation(
                        ip, security.autoban_violations, security.autoban_seconds
                    )
                    log.warning(
                        "client=%s method=%s path=%s ratelimit=general retry_after=%d autoban=%s",
                        ip,
                        method,
                        path,
                        retry,
                        banned,
                    )
                    response = JSONResponse(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        headers={"Retry-After": str(retry)},
                        content={
                            "error": ErrorCode.RATE_LIMITED,
                            "retry_after": retry,
                            "message": "请求过于频繁，请稍后再试。",
                        },
                    )
                # 3) 板块级限流（仅探索计算 POST /api/board/*）
                elif is_board:
                    ok2, retry2 = await security.board_limiter.check(ip)
                    if not ok2:
                        banned = security.blacklist.register_violation(
                            ip, security.autoban_violations, security.autoban_seconds
                        )
                        log.warning(
                            "client=%s method=%s path=%s ratelimit=board retry_after=%d autoban=%s",
                            ip,
                            method,
                            path,
                            retry2,
                            banned,
                        )
                        response = JSONResponse(
                            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            headers={"Retry-After": str(retry2)},
                            content={
                                "error": ErrorCode.RATE_LIMITED,
                                "retry_after": retry2,
                                "message": "探索计算请求过于频繁，请稍后再试。",
                            },
                        )

        if response is None:
            # 4) 异常判定（频率规则）：对每个未被拉黑的 api 请求计数
            if anomaly_enabled and anomaly_detector and is_api:
                hit, reasons = anomaly_detector.observe(ip)
                if hit:
                    anomaly_reasons = reasons
                    log.warning(
                        "client=%s method=%s path=%s ANOMALY freq ban ip for %ds",
                        ip,
                        method,
                        path,
                        settings.anomaly_ban_seconds,
                    )
                    if audit_store:
                        try:
                            await audit_store.record_anomaly(
                                {
                                    "request_id": uuid.uuid4().hex,
                                    "client_ip": ip,
                                    "rule": "frequency",
                                    "detail": ";".join(reasons),
                                    "action": f"blacklist_{settings.anomaly_ban_seconds}s",
                                }
                            )
                        except Exception:
                            log.warning("异常事件入库失败（已忽略）", exc_info=True)
            # 5) 计数（通过后）
            await security.general_limiter.hit(ip)
            if is_board:
                await security.board_limiter.hit(ip)

            # 6) 读取请求体用于审计（仅写操作；Starlette 会缓存 body，下游仍可正常解析）
            if is_api and method in ("POST", "PUT", "PATCH"):
                try:
                    raw = await request.body()
                    if raw:
                        # 凭据脱敏：登录/注册等含明文密码的接口，写审计前先遮蔽敏感字段。
                        # 顺序要点：**先脱敏、后截断**。若先截断，JSON 被切碎后只能走
                        # 正则回退路径，脱敏可靠性下降（尤其在截断处正好落在密码值时）。
                        req_body = _redact_sensitive(raw.decode("utf-8", "replace"))[
                            : settings.audit_body_max_bytes
                        ]
                except Exception:
                    req_body = ""

            # 7) 处理请求
            start = time.time()
            response = await call_next(request)
            dur_ms = (time.time() - start) * 1000
        # else: 被拦截（访问规则/黑名单/限流）——仍纳入审计，但无响应体 / 耗时

        # 8) 审计 + 访问统计埋点
        #    - 触发条件由「仅 api」放宽为「page + api」，从而统计页面级 PV；
        #      静态资源（css/js/图片/字体）归为 asset，既不入库也不计 PV，避免噪声。
        #    - 审计 DB 写入为原生 async（await 不阻塞事件循环）。
        if audit_enabled:
            resp_status = response.status_code
            # 已登录用户身份：从会话 Cookie 解析（auth 关闭 / 未登录则为空）
            user_id = None
            username = ""
            if user_store is not None:
                u = await resolve_session_user(request, user_store, settings.session_cookie_name)
                if u is not None:
                    user_id, username = u.id, u.username
            content_type = response.headers.get("content-type", "")
            route_type = _classify_route(path, content_type)
            if route_type != "asset":  # 静态资源不审计、不计访问
                snippet = ""
                try:
                    body = getattr(response, "body", None)
                    if body and len(body) <= settings.audit_body_max_bytes:
                        # 同样脱敏响应体中的敏感字段（纵深防御；登录响应不含密码，但统一处理）
                        snippet = _redact_sensitive(body.decode("utf-8", "replace"))
                except Exception:
                    snippet = ""
                record = {
                    "request_id": uuid.uuid4().hex,
                    "client_ip": ip,
                    "client_device": derive_device(request.headers.get("user-agent", "")),
                    "user_agent": request.headers.get("user-agent", ""),
                    "method": method,
                    "path": path,
                    # query 同样要脱敏：管理报表支持 ?token=、邮箱验证链接为
                    # /verify-email?token=，原样落盘等于把凭据写进审计文件与审计库。
                    "query": _redact_query(request.url.query),
                    "request_body": req_body,
                    "status_code": resp_status,
                    "duration_ms": round(dur_ms, 2),
                    "is_anomaly": bool(anomaly_reasons),
                    "anomaly_reasons": ";".join(anomaly_reasons),
                    "response_snippet": snippet,
                    # 访问统计维度（v1.7.0）：访客指纹 / 来源页 / 路由类型
                    "visitor_id": _compute_visitor_id(ip, request.headers.get("user-agent", "")),
                    "referer": request.headers.get("referer", ""),
                    "route_type": route_type,
                    # 已登录用户身份（v1.8.0）：审计可按用户维度追溯
                    "user_id": user_id,
                    "username": username,
                }
                log_audit_event(record)
                if audit_store:
                    # best-effort：审计入库失败（如瞬时锁/磁盘满）绝不能把已确定的
                    # 403/200 响应变成 500。异常仅记录，不影响主响应返回。
                    try:
                        await audit_store.record_audit(record)
                    except Exception:
                        log.warning("审计入库失败（已忽略，不影响主响应）", exc_info=True)

        # 生产（HTTPS）部署：开启 Secure Cookie 时一并下发 HSTS，强制客户端仅经 TLS 访问，
        # 避免凭据在明文 HTTP 下被窃听。本地 http 开发（session_cookie_secure=false）不加。
        if settings.session_cookie_secure:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        return response

    return middleware
