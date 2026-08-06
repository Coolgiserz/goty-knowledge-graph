"""FastAPI 应用：数据探索 API + 静态资源托管 + 安全防护 + 异步任务。

- GET  /api/meta         数据概览 + 数据有效性(sha 守卫) + 探索开关 + 板块清单
- GET  /api/boards       各板块的参数 schema / 解读默认值 / 解读文本
- POST /api/board/{name} 同步计算（调试用；耗资源，受总开关 + 限流约束）
- POST /api/jobs         提交异步探索任务（立即返回 job_id，不阻塞）
- GET  /api/jobs         列出任务（自己的；持令牌 + ?scope=all 看全部）
- GET  /api/jobs/queue   全局队列负荷快照（运行中/等待/并发上限）
- GET  /api/jobs/{id}   任务状态 + 结果 + 排队位次（轮询）
- DELETE /api/jobs/{id} 取消 pending 任务

安全防护（云端 demo）：黑名单(403) + 两档限流(429) + 结构化日志（见 ratelimit/logging_config）。
探索总开关：GOTY_ENABLE_EXPLORATION（默认关）→ 关时仅托管原 v1 只读图谱/表格，
            /api/jobs 与 /api/board 返回 403；开时可用异步探索 + 任务管理。
            GOTY_EXPLORE_TOKEN（可选）：开启后提交任务需携带有效令牌。

静态托管：
- 探索开启：/ 探索 SPA（site/explorer/），/graph/ 原 v1 静态站点
- 探索关闭（默认/快速模式）：/ 原 v1 只读图谱/表格浏览页（site/）

运行：uvicorn api.app:app --host 0.0.0.0 --port 8000
"""
import os
import time

from fastapi import FastAPI, HTTPException, Request
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
from .tasks import TaskManager, TooManyPending

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLORER_DIR = os.path.join(ROOT, "site", "explorer")
SITE_DIR = os.path.join(ROOT, "site")

log = setup_logging()
cfg = SecurityConfig()
general_limiter = Limiter(cfg.general_max, cfg.general_window)
board_limiter = Limiter(cfg.board_max, cfg.board_window)
blacklist = Blacklist(cfg.seed_blacklist, cfg.blacklist_file)

# 探索总开关 / 令牌 / 任务参数（云端 demo 取向）
EXPLORATION_ENABLED = os.environ.get(
    "GOTY_ENABLE_EXPLORATION", "false").strip().lower() in ("1", "true", "yes", "on")
EXPLORE_TOKEN = os.environ.get("GOTY_EXPLORE_TOKEN", "").strip()
TASK_WORKERS = max(1, int(os.environ.get("GOTY_TASK_WORKERS", "2")))
MAX_PENDING = max(1, int(os.environ.get("GOTY_MAX_PENDING", "5")))
tasks_mgr = TaskManager(max_workers=TASK_WORKERS, max_pending_per_owner=MAX_PENDING)


class SecurityMiddleware(BaseHTTPMiddleware):
    """黑名单 + 两档限流 + 请求日志。"""

    async def dispatch(self, request, call_next):
        ip = get_client_ip(request, cfg.trust_proxy)
        path = request.url.path
        is_board = path.startswith("/api/board/") and request.method == "POST"

        if blacklist.is_blacklisted(ip):
            log.warning("client=%s method=%s path=%s BLOCKED blacklisted",
                        ip, request.method, path)
            return JSONResponse(
                status_code=403,
                content={"error": "blacklisted",
                         "message": "您的访问已被限制，请联系管理员。"},
            )

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

        general_limiter.hit(ip)
        if is_board:
            board_limiter.hit(ip)

        start = time.time()
        response = await call_next(request)
        dur_ms = (time.time() - start) * 1000
        log.info("client=%s method=%s path=%s status=%d dur_ms=%.1f",
                 ip, request.method, path, response.status_code, dur_ms)
        return response


app = FastAPI(title="GOTY 知识图谱 · 数据探索 API", version="1.2")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityMiddleware)


# ---------------- 探索开关与身份 ----------------
def extract_token(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    t = request.headers.get("x-explore-token")
    if t:
        return t.strip()
    t = request.query_params.get("token")
    return t.strip() if t else ""


def resolve_owner(request: Request):
    """探索已开启时解析任务归属。返回 (owner, err)。"""
    if EXPLORE_TOKEN:
        if extract_token(request) != EXPLORE_TOKEN:
            return None, "invalid_or_missing_token"
        return "admin", None
    uid = request.headers.get("x-user-id")
    if uid:
        return uid.strip()[:40], None
    return f"anon:{get_client_ip(request, cfg.trust_proxy)}", None


def _forbid_if_disabled():
    if not EXPLORATION_ENABLED:
        raise HTTPException(status_code=403, detail="exploration_disabled")


def _job_dict(t, stats: dict, include_result: bool = False) -> dict:
    """把任务基础信息 + 排队位次 + 全局队列快照合并为一个返回体。"""
    pos = tasks_mgr.queue_position(t.id)
    d = t.to_dict(include_result=include_result, position=pos)
    d["queue_running"] = stats["running"]
    d["queue_waiting"] = stats["waiting"]
    d["queue_max_workers"] = stats["max_workers"]
    return d


# ---------------- 路由 ----------------
class BoardReq(BaseModel):
    params: dict = {}


class JobReq(BaseModel):
    board: str
    params: dict = {}


@app.get("/api/meta")
def meta():
    return {
        "counts": node_counts(),
        "sha256": SHA[:16],
        "data_matches_baseline": data_matches_baseline(),
        "exploration_enabled": EXPLORATION_ENABLED,
        "boards": [{"name": b.name, "label": b.label, "description": b.description}
                   for b in all_boards()],
    }


@app.get("/api/boards")
def boards():
    return {"boards": [board_meta(b) for b in all_boards()]}


@app.post("/api/board/{name}")
def board(name: str, req: BoardReq):
    _forbid_if_disabled()
    res = run_board(name, req.params, data_matches_baseline())
    if res is None:
        raise HTTPException(status_code=404, detail=f"未知探索板块: {name}")
    return res


@app.post("/api/jobs")
def create_job(req: JobReq, request: Request):
    _forbid_if_disabled()
    owner, err = resolve_owner(request)
    if err == "invalid_or_missing_token":
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized",
                     "message": "需要有效的访问令牌才能提交探索任务。"},
        )
    if req.board not in {b.name for b in all_boards()}:
        raise HTTPException(status_code=404, detail=f"未知探索板块: {req.board}")
    try:
        t = tasks_mgr.create(req.board, req.params, owner)
    except TooManyPending as e:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "30"},
            content={"error": "too_many_pending", "retry_after": 30,
                     "message": str(e)},
        )
    log.info("job=%s owner=%s board=%s created", t.id, owner, req.board)
    return {"id": t.id, "status": t.status, "board": t.board, "owner": t.owner}


@app.get("/api/jobs")
def list_jobs(request: Request):
    _forbid_if_disabled()
    owner, _ = resolve_owner(request)
    all_scope = bool(EXPLORE_TOKEN) and extract_token(request) == EXPLORE_TOKEN \
        and request.query_params.get("scope") == "all"
    items = tasks_mgr.list(owner=owner, all_scope=all_scope)
    stats = tasks_mgr.queue_stats()
    return {"jobs": [_job_dict(t, stats) for t in items],
            "scope": "all" if all_scope else "self"}


@app.get("/api/jobs/queue")
def queue_status(request: Request):
    """轻量队列负荷快照，供前端任务面板常驻显示（无需列出个人任务即可查看）。"""
    _forbid_if_disabled()
    return {"queue": tasks_mgr.queue_stats()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    _forbid_if_disabled()
    t = tasks_mgr.get(job_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    owner, _ = resolve_owner(request)
    all_scope = bool(EXPLORE_TOKEN) and extract_token(request) == EXPLORE_TOKEN
    if not all_scope and t.owner != owner:
        raise HTTPException(status_code=404, detail="任务不存在")
    stats = tasks_mgr.queue_stats()
    return _job_dict(t, stats, include_result=True)


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str, request: Request):
    _forbid_if_disabled()
    t = tasks_mgr.get(job_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    owner, _ = resolve_owner(request)
    all_scope = bool(EXPLORE_TOKEN) and extract_token(request) == EXPLORE_TOKEN
    if not all_scope and t.owner != owner:
        raise HTTPException(status_code=404, detail="任务不存在")
    canceled = tasks_mgr.cancel(job_id)
    return {"id": job_id, "canceled": canceled}


# 静态资源：先注册 API 路由，最后按模式挂载静态目录。
# 探索开启 → / 探索 SPA，/graph/ 原 v1；探索关闭（默认/快速模式）→ / 原 v1 只读浏览。
if EXPLORATION_ENABLED:
    if os.path.isdir(EXPLORER_DIR):
        app.mount("/", StaticFiles(directory=EXPLORER_DIR, html=True), name="explorer")
    if os.path.isdir(SITE_DIR):
        app.mount("/graph", StaticFiles(directory=SITE_DIR, html=True), name="graph")
else:
    if os.path.isdir(SITE_DIR):
        app.mount("/", StaticFiles(directory=SITE_DIR, html=True), name="graph")
