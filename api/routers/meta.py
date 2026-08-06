"""元数据路由：数据概览、有效性守卫、探索开关、板块清单。

``GET /api/meta`` 同时承载「数据是否有效（sha256 守卫）」与「探索开关」两类信息，
前端据此决定展示默认静态分析还是提示用户进入探索；无论开关是否开启都可访问。
"""

from fastapi import APIRouter, Depends

from ..config import Settings
from ..deps import get_settings_dep
from ..graph_loader import SHA, data_matches_baseline, node_counts
from ..registry import all_boards
from ..schemas import MetaBoardSummary, MetaResponse

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta", response_model=MetaResponse)
def meta(settings: Settings = Depends(get_settings_dep)):
    """数据概览 + 数据有效性(sha 守卫) + 探索开关 + 板块清单。"""
    return MetaResponse(
        counts=node_counts(),
        sha256=SHA[:16],
        data_matches_baseline=data_matches_baseline(),
        exploration_enabled=settings.enable_exploration,
        boards=[
            MetaBoardSummary(name=b.name, label=b.label, description=b.description)
            for b in all_boards()
        ],
    )
