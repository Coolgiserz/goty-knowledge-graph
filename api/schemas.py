"""接口响应模型（强类型 ``response_model``）。

字段名与历史上游前端消费的一致，仅做类型约束，不改变 JSON 结构：
- ``/api/meta``        -> :class:`MetaResponse`
- ``/api/boards``      -> ``{"boards": [BoardMeta]}``
- ``/api/board/{name}``-> :class:`BoardRunResult`（panels/tables/metrics 为动态结构，宽松定型）
- ``/api/jobs``        -> ``{"jobs": [JobView], "scope": str}``
- ``/api/jobs/{id}``   -> :class:`JobView`
- ``/api/jobs/queue``  -> ``{"queue": QueueStats}``
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class MetaBoardSummary(BaseModel):
    name: str
    label: str
    description: str


class MetaResponse(BaseModel):
    counts: dict[str, int]
    sha256: str
    data_matches_baseline: bool | None
    exploration_enabled: bool
    graph_backend: str
    boards: list[MetaBoardSummary]
    auth_enabled: bool
    auth_email_required: bool


class ParamSpecView(BaseModel):
    key: str
    label: str
    type: str
    default: Any = None
    options: list[Any] | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    help: str = ""
    group: str = ""


class BoardMeta(BaseModel):
    name: str
    label: str
    description: str
    params: list[ParamSpecView]
    interpretation_defaults: dict[str, Any]
    interpretation: str


class ValidityView(BaseModel):
    data_matches_baseline: bool | None
    interpretation_valid: bool
    invalid_reasons: list[dict[str, Any]]


class BoardRunResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    board: str
    params: dict[str, Any]
    interpretation: str
    validity: ValidityView
    panels: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}


class JobView(BaseModel):
    id: str
    board: str
    status: str
    owner: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    queue_position: int | None = None
    queue_running: int | None = None
    queue_waiting: int | None = None
    queue_max_workers: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class QueueStats(BaseModel):
    running: int
    waiting: int
    max_workers: int


class BoardsResponse(BaseModel):
    boards: list[BoardMeta]


class JobCreated(BaseModel):
    id: str
    status: str
    board: str
    owner: str


class JobsListResponse(BaseModel):
    jobs: list[JobView]
    scope: str


class QueueResponse(BaseModel):
    queue: QueueStats
