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
import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from .anomaly import AnomalyDetector
from .audit.store import AuditStore
from .config import Settings
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
                    status_code=403,
                    content={
                        "error": "blocked",
                        "message": "该请求来源被拒绝，请使用真实浏览器访问。",
                    },
                )
                break

        # 1) 黑名单
        if response is None:
            if security.blacklist.is_blacklisted(ip):
                log.warning("client=%s method=%s path=%s BLOCKED blacklisted", ip, method, path)
                response = JSONResponse(
                    status_code=403,
                    content={"error": "blacklisted", "message": "您的访问已被限制，请联系管理员。"},
                )
            else:
                # 2) 一般限流
                ok, retry = security.general_limiter.check(ip)
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
                        status_code=429,
                        headers={"Retry-After": str(retry)},
                        content={
                            "error": "rate_limited",
                            "retry_after": retry,
                            "message": "请求过于频繁，请稍后再试。",
                        },
                    )
                # 3) 板块级限流（仅探索计算 POST /api/board/*）
                elif is_board:
                    ok2, retry2 = security.board_limiter.check(ip)
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
                            status_code=429,
                            headers={"Retry-After": str(retry2)},
                            content={
                                "error": "rate_limited",
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
            security.general_limiter.hit(ip)
            if is_board:
                security.board_limiter.hit(ip)

            # 6) 读取请求体用于审计（仅写操作；Starlette 会缓存 body，下游仍可正常解析）
            if is_api and method in ("POST", "PUT", "PATCH"):
                try:
                    raw = await request.body()
                    if raw:
                        req_body = raw[: settings.audit_body_max_bytes].decode("utf-8", "replace")
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
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            route_type = _classify_route(path, content_type)
            if route_type != "asset":  # 静态资源不审计、不计访问
                snippet = ""
                try:
                    body = getattr(response, "body", None)
                    if body and len(body) <= settings.audit_body_max_bytes:
                        snippet = body.decode("utf-8", "replace")
                except Exception:
                    snippet = ""
                record = {
                    "request_id": uuid.uuid4().hex,
                    "client_ip": ip,
                    "client_device": derive_device(request.headers.get("user-agent", "")),
                    "user_agent": request.headers.get("user-agent", ""),
                    "method": method,
                    "path": path,
                    "query": request.url.query,
                    "request_body": req_body,
                    "status_code": status,
                    "duration_ms": round(dur_ms, 2),
                    "is_anomaly": bool(anomaly_reasons),
                    "anomaly_reasons": ";".join(anomaly_reasons),
                    "response_snippet": snippet,
                    # 访问统计维度（v1.7.0）：访客指纹 / 来源页 / 路由类型
                    "visitor_id": _compute_visitor_id(ip, request.headers.get("user-agent", "")),
                    "referer": request.headers.get("referer", ""),
                    "route_type": route_type,
                }
                log_audit_event(record)
                if audit_store:
                    # best-effort：审计入库失败（如瞬时锁/磁盘满）绝不能把已确定的
                    # 403/200 响应变成 500。异常仅记录，不影响主响应返回。
                    try:
                        await audit_store.record_audit(record)
                    except Exception:
                        log.warning("审计入库失败（已忽略，不影响主响应）", exc_info=True)

        return response

    return middleware
