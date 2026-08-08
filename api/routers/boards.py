"""探索板块路由：参数 schema 与同步（调试用）计算。

- ``GET  /api/boards``     各板块的参数 schema / 解读默认值 / 解读文本（无需开关）。
- ``POST /api/board/{name}`` 同步计算（调试用；耗资源，受总开关约束 + 中间件限流）。
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import registry
from ..config import Settings
from ..constants import HTTP, ErrorCode
from ..deps import get_settings_dep, require_exploration
from ..graph_loader import data_matches_baseline
from ..schemas import BoardMeta, BoardRunResult, BoardsResponse

router = APIRouter(prefix="/api", tags=["boards"])


class BoardReq(BaseModel):
    params: dict = {}


@router.get("/boards", response_model=BoardsResponse)
def boards():
    """所有板块的元信息（参数控件声明 + 默认解读）。"""
    return BoardsResponse(
        boards=[BoardMeta(**registry.board_meta(b)) for b in registry.all_boards()]
    )


@router.post("/board/{name}", response_model=BoardRunResult)
def board(name: str, req: BoardReq, settings: Settings = Depends(get_settings_dep)):
    """对某个板块用给定参数做同步计算（调试 / 轻量场景）。

    受探索总开关保护：关闭时返回 403。生产重负载请改用异步 ``POST /api/jobs``。
    """
    require_exploration(settings)
    res = registry.run_board(name, req.params, data_matches_baseline())
    if res is None:
        raise HTTPException(status_code=HTTP.NOT_FOUND, detail=f"{ErrorCode.UNKNOWN_BOARD}: {name}")
    return BoardRunResult(**res)
