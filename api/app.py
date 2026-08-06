"""FastAPI 应用：数据探索 API + 静态资源托管 + 安全防护。

- GET  /api/meta      数据概览 + 数据有效性(sha 守卫) + 板块清单
- GET  /api/boards    各板块的参数 schema / 解读默认值 / 解读文本
- POST /api/board/{name}  传入参数 → 计算 → 返回面板/表格/指标/有效性

安全防护（云端 demo）：
- 黑名单拦截（403，含环境变量种子 + 自动封禁）
- 两档限流：一般请求（宽松）/ 探索计算（严格，429）
- 结构化请求与告警日志

静态托管：
- /            新探索 SPA（site/explorer/）
- /graph/      原图谱浏览页（site/）

运行：uvicorn api.app:app --host 0.0.0.0 --port 8000
"""
import os
import time

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel

from . import tools  # 触发板块注册（导入即注册）
from .registry import all_boards, board_meta, run_board
from .graph_loader import node_counts, data_matches_baseline, SHA
from .logging_config import setup_logging
from .ratelimit import (
    SecurityConfig,
    Limiter,
    Blacklist,
    get_client_ip,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLORER_DIR = os.path.join(ROOT, "site", "explorer")
SITE_DIR = os.path.join(ROOT, "site")

log = setup_logging()
cfg = SecurityConfig()
general_limiter = Limiter(cfg.general_max, cfg.general_window)
board_limiter = Limiter(cfg.board_max, cfg.board_window)
blacklist = Blacklist(cfg.seed_blacklist, cfg.blacklist_file)


class SecurityMiddleware(BaseHTTPMiddleware):
    """黑名单 + 两档限流 + 请求日志。"""

    async def dispatch(self, request, call_next):
        ip = get_client_ip(request, cfg.trust_proxy)
        path = request.url.path
        is_board = path.startswith("/api/board/") and request.method == "POST"

        # 1) 黑名单：直接拒绝
        if blacklist.is_blacklisted(ip):
            log.warning("client=%s method=%s path=%s BLOCKED blacklisted",
                        ip, request.method, path)
            return JSONResponse(
                status_code=403,
                content={"error": "blacklisted",
                         "message": "您的访问已被限制，请联系管理员。"},
            )

        # 2) 一般请求限流
        ok, retry = general_limiter.check(ip)
        if not ok:
            banned = blacklist.register_violation(
                ip, cfg.autoban_violations, cfg.autoban_seconds)
            log.warning(
                "client=%s method=%s path=%s ratelimit=general retry_after=%d autoban=%s",
                ip, request.method, path, retry, banned)
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(retry)},
                content={"error": "rate_limited", "retry_after": retry,
                          "message": "请求过于频繁，请稍后再试。"},
            )

        # 3) 探索计算单独限流（重负载入口）
        if is_board:
            ok2, retry2 = board_limiter.check(ip)
            if not ok2:
                banned = blacklist.register_violation(
                    ip, cfg.autoban_violations, cfg.autoban_seconds)
                log.warning(
                    "client=%s method=%s path=%s ratelimit=board retry_after=%d autoban=%s",
                    ip, request.method, path, retry2, banned)
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(retry2)},
                    content={"error": "rate_limited", "retry_after": retry2,
                             "message": "探索计算请求过于频繁，请稍后再试。"},
                )

        # 通过：计数并记录耗时
        general_limiter.hit(ip)
        if is_board:
            board_limiter.hit(ip)

        start = time.time()
        response = await call_next(request)
        dur_ms = (time.time() - start) * 1000
        log.info("client=%s method=%s path=%s status=%d dur_ms=%.1f",
                 ip, request.method, path, response.status_code, dur_ms)
        return response


app = FastAPI(title="GOTY 知识图谱 · 数据探索 API", version="1.1")

# 跨域：前端可与 API 分离部署（也可同源）；放在最外层，确保 429/403 也带 CORS 头
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# 安全防护中间件（内层，先于路由执行）
app.add_middleware(SecurityMiddleware)


class BoardReq(BaseModel):
    params: dict = {}


@app.get("/api/meta")
def meta():
    return {
        "counts": node_counts(),
        "sha256": SHA[:16],
        "data_matches_baseline": data_matches_baseline(),
        "boards": [{"name": b.name, "label": b.label, "description": b.description}
                   for b in all_boards()],
    }


@app.get("/api/boards")
def boards():
    return {"boards": [board_meta(b) for b in all_boards()]}


@app.post("/api/board/{name}")
def board(name: str, req: BoardReq):
    res = run_board(name, req.params, data_matches_baseline())
    if res is None:
        raise HTTPException(status_code=404, detail=f"未知探索板块: {name}")
    return res


# 静态资源：先注册 API 路由，最后再挂载静态目录（明确路径优先于通配挂载）。
# 目录不存在时跳过挂载，保证 API 可独立于前端运行 / 部署不崩溃。
if os.path.isdir(SITE_DIR):
    app.mount("/graph", StaticFiles(directory=SITE_DIR, html=True), name="graph")
if os.path.isdir(EXPLORER_DIR):
    app.mount("/", StaticFiles(directory=EXPLORER_DIR, html=True), name="explorer")
