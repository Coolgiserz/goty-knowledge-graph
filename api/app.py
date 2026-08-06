"""FastAPI 应用：数据探索 API + 静态资源托管。

- GET  /api/meta      数据概览 + 数据有效性(sha 守卫) + 板块清单
- GET  /api/boards    各板块的参数 schema / 解读默认值 / 解读文本
- POST /api/board/{name}  传入参数 → 计算 → 返回面板/表格/指标/有效性

静态托管：
- /            新探索 SPA（site/explorer/）
- /graph/      原图谱浏览页（site/）

运行：uvicorn api.app:app --reload --port 8000
"""
import os

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import tools  # 触发板块注册（导入即注册）
from .registry import all_boards, board_meta, run_board
from .graph_loader import node_counts, data_matches_baseline, SHA

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPLORER_DIR = os.path.join(ROOT, "site", "explorer")
SITE_DIR = os.path.join(ROOT, "site")

app = FastAPI(title="GOTY 知识图谱 · 数据探索 API", version="1.0")

# 允许跨域：前端与 API 可分离部署（也可同源挂载）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


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
