#!/usr/bin/env bash
# 本地快速启动：数据探索 API + 静态站点（FastAPI/uvicorn）。
# 两种模式由环境变量 GOTY_ENABLE_EXPLORATION 控制：
#   关（默认 / 洞察模式）：/ 提供「原始数据页 + 原始洞察页」只读浏览，/api/jobs 等返回 403
#   开（探索模式）      ：/ 同上，额外挂载 /explore 探索 SPA（参数化数据挖掘）
# 用法： bash scripts/serve_api.sh [端口]
#
# 云端部署可用环境变量调参（详见 README「安全与限流」）：
#   GOTY_RATE_LIMIT_MAX / GOTY_BOARD_LIMIT_MAX / GOTY_AUTOBAN_VIOLATIONS
#   GOTY_BLACKLIST / GOTY_BLACKLIST_FILE / GOTY_LOG_FILE / GOTY_TRUST_PROXY ...
set -euo pipefail
PORT="${1:-8080}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# 复用项目隔离 venv（含 fastapi/uvicorn/scikit-learn 等）；可用 PYTHON 环境变量覆盖。
PY="${PYTHON:-/Users/tarnished/.workbuddy/binaries/python/envs/default/bin/python}"
export PYTHONPATH="$ROOT_DIR:${PYTHONPATH:-}"

# 关闭 uvicorn 自带的 access log，统一由 api/logging_config 输出结构化请求日志，
# 避免重复且信息更全（含客户端 IP / 耗时 / 限流与封禁告警）。
EXPLORATION="${GOTY_ENABLE_EXPLORATION:-false}"

echo "🚀 已启动： http://localhost:${PORT}"
echo "   洞察页（原始数据 + 原始洞察）： http://localhost:${PORT}/"
if [ "$EXPLORATION" = "true" ]; then
  echo "   探索页（参数化数据挖掘）：        http://localhost:${PORT}/explore/"
fi
echo "   按 Ctrl+C 停止。"
exec "$PY" -m uvicorn api.app:app --host 0.0.0.0 --port "$PORT" --no-access-log
