#!/usr/bin/env bash
# 本地快速启动：数据探索 API + 探索 SPA（FastAPI/uvicorn）。
# 同时托管：
#   /            探索 SPA（site/explorer/，可调参数做数据挖掘）
#   /graph/      原图谱浏览页（site/，vis-network 力导向图）
#   /api/*       探索 API（板块 schema / 计算 / 双有效性判定）
# 用法： bash scripts/serve_api.sh [端口]
set -euo pipefail
PORT="${1:-8080}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 复用项目隔离 venv（含 fastapi/uvicorn/scikit-learn 等）；可用 PYTHON 环境变量覆盖。
PY="${PYTHON:-/Users/tarnished/.workbuddy/binaries/python/envs/default/bin/python}"
export PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}"

echo "🚀 数据探索已启动： http://localhost:${PORT}"
echo "   探索页：  http://localhost:${PORT}/"
echo "   原图谱：  http://localhost:${PORT}/graph/"
echo "   按 Ctrl+C 停止。"
exec "$PY" -m uvicorn api.app:app --host 0.0.0.0 --port "$PORT"
