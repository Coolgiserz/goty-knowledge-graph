"""异步探索任务路由：提交、轮询、列表、取消、队列负荷。

耗时计算（社区发现 / 嵌入 / PageRank 等）不阻塞请求：``POST /api/jobs`` 立即返回
``job_id``，计算在后台线程池跑，前端轮询 ``GET /api/jobs/{id}`` 拿结果。全部受探索
总开关保护（关闭时 403）。
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import Settings
from ..deps import (
    get_security,
    get_settings_dep,
    get_tasks,
    is_admin_scope,
    require_exploration,
    resolve_owner,
)
from ..registry import all_boards
from ..schemas import JobCreated, JobsListResponse, JobView, QueueResponse, QueueStats
from ..security import SecurityContext
from ..tasks import TaskManager, TooManyPending

router = APIRouter(prefix="/api", tags=["jobs"])


class JobReq(BaseModel):
    board: str
    params: dict = {}


def _job_dict(task, tasks: TaskManager, stats: dict, include_result: bool = False) -> dict:
    """任务基础信息 + 排队位次 + 全局队列快照，合并为一个返回体。"""
    d = task.to_dict(include_result=include_result, position=tasks.queue_position(task.id))
    d["queue_running"] = stats["running"]
    d["queue_waiting"] = stats["waiting"]
    d["queue_max_workers"] = stats["max_workers"]
    return d


@router.post("/jobs", response_model=JobCreated, status_code=200)
def create_job(
    req: JobReq,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    security: SecurityContext = Depends(get_security),
    tasks: TaskManager = Depends(get_tasks),
):
    require_exploration(settings)
    owner, err = resolve_owner(request, settings, security)
    if err == "invalid_or_missing_token":
        return JSONResponse(
            status_code=401,
            content={"error": "unauthorized", "message": "需要有效的访问令牌才能提交探索任务。"},
        )
    if req.board not in {b.name for b in all_boards()}:
        raise HTTPException(status_code=404, detail=f"未知探索板块: {req.board}")
    try:
        t = tasks.create(req.board, req.params, owner)
    except TooManyPending as e:
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": "30"},
            content={"error": "too_many_pending", "retry_after": 30, "message": str(e)},
        )
    return JobCreated(id=t.id, status=t.status, board=t.board, owner=t.owner)


@router.get("/jobs", response_model=JobsListResponse)
def list_jobs(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    security: SecurityContext = Depends(get_security),
    tasks: TaskManager = Depends(get_tasks),
):
    require_exploration(settings)
    owner, _ = resolve_owner(request, settings, security)
    all_scope = is_admin_scope(request, settings) and request.query_params.get("scope") == "all"
    items = tasks.list(owner=owner, all_scope=all_scope)
    stats = tasks.queue_stats()
    return JobsListResponse(
        jobs=[JobView(**_job_dict(t, tasks, stats)) for t in items],
        scope="all" if all_scope else "self",
    )


@router.get("/jobs/queue", response_model=QueueResponse)
def queue_status(
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    tasks: TaskManager = Depends(get_tasks),
):
    """轻量队列负荷快照，供前端任务面板常驻显示（无需列出个人任务即可查看）。"""
    require_exploration(settings)
    return QueueResponse(queue=QueueStats(**tasks.queue_stats()))


@router.get("/jobs/{job_id}", response_model=JobView)
def get_job(
    job_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    security: SecurityContext = Depends(get_security),
    tasks: TaskManager = Depends(get_tasks),
):
    require_exploration(settings)
    t = tasks.get(job_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    owner, _ = resolve_owner(request, settings, security)
    if not is_admin_scope(request, settings) and t.owner != owner:
        raise HTTPException(status_code=404, detail="任务不存在")
    stats = tasks.queue_stats()
    return JobView(**_job_dict(t, tasks, stats, include_result=True))


@router.delete("/jobs/{job_id}")
def cancel_job(
    job_id: str,
    request: Request,
    settings: Settings = Depends(get_settings_dep),
    security: SecurityContext = Depends(get_security),
    tasks: TaskManager = Depends(get_tasks),
):
    require_exploration(settings)
    t = tasks.get(job_id)
    if not t:
        raise HTTPException(status_code=404, detail="任务不存在")
    owner, _ = resolve_owner(request, settings, security)
    if not is_admin_scope(request, settings) and t.owner != owner:
        raise HTTPException(status_code=404, detail="任务不存在")
    canceled = tasks.cancel(job_id)
    return {"id": job_id, "canceled": canceled}
