#!/usr/bin/env bash
# 本地快速启动：在当前目录启动一个静态文件服务器，浏览知识图谱网站。
# 用法： bash scripts/serve.sh [端口]
set -euo pipefail
PORT="${1:-8080}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/site"
echo "🚀 知识图谱网站已启动： http://localhost:${PORT}"
echo "   按 Ctrl+C 停止。"
python3 -m http.server "$PORT"
