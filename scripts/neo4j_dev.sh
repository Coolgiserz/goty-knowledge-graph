#!/usr/bin/env bash
# 启动一个**开发用** Neo4j 容器并自动导入本数据集。
#
# 与 scripts/neo4j_import.sh（默认端口 7474/7687，容器名 neo4j-goty）区分：
#   本脚本使用**非默认端口**（HTTP 7475 / Bolt 7688）与独立容器名
#   neo4j-goty-dev，避免与你本机已在运行的 Neo4j 实例（或 docker-compose 的全栈）
#   抢占端口。适合「接后端可选查询层」时本地试跑 Cypher。
#
# 用法： bash scripts/neo4j_dev.sh
# 完成后： Neo4j Browser http://localhost:7475  （neo4j / password123）
# 停止：   docker rm -f neo4j-goty-dev
# 清数据： docker volume rm neo4j-goty-dev-data
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CSV_DIR="$ROOT_DIR/data/csv"
CYPHER="$ROOT_DIR/scripts/init.cypher"
NAME="neo4j-goty-dev"
# 镜像可经 .env 的 NEO4J_IMAGE 覆盖（例如 Docker Hub 拉不动时改用国内/ARM 镜像）。
# 默认走 Docker Hub 多架构标签，Apple Silicon 会自动选 arm64。
IMAGE="neo4j:5.26-community"
if [ -f "$ROOT_DIR/.env" ] && grep -qE '^NEO4J_IMAGE=' "$ROOT_DIR/.env"; then
  IMAGE="$(grep -E '^NEO4J_IMAGE=' "$ROOT_DIR/.env" | head -1 | cut -d= -f2-)"
fi
# 若仓库根 .env 设了 NEO4J_PASSWORD，则沿用；否则用默认弱密码（仅本地演示）
PASS="password123"
if [ -f "$ROOT_DIR/.env" ] && grep -qE '^NEO4J_PASSWORD=' "$ROOT_DIR/.env"; then
  PASS="$(grep -E '^NEO4J_PASSWORD=' "$ROOT_DIR/.env" | head -1 | cut -d= -f2-)"
fi
HTTP_PORT="7475"   # 非默认（默认 7474）
BOLT_PORT="7688"   # 非默认（默认 7687）

if ! command -v docker >/dev/null 2>&1; then
  echo "✗ 未检测到 docker，请先安装 Docker：https://docs.docker.com/get-docker/" >&2
  exit 1
fi

# 若已存在同名容器则先移除
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "→ 发现已有容器 ${NAME}，正在移除…"
  docker rm -f "$NAME" >/dev/null
fi

echo "→ 启动 Neo4j 开发容器（镜像 ${IMAGE}，端口 HTTP:$HTTP_PORT / Bolt:${BOLT_PORT}，数据卷 ${NAME}-data）…"
docker run -d --name "$NAME" \
  -p "$HTTP_PORT:7474" -p "$BOLT_PORT:7687" \
  -e NEO4J_AUTH="neo4j/$PASS" \
  -e NEO4J_dbms_directories_import=/import \
  -v "$CSV_DIR:/import/csv:ro" \
  -v "${NAME}-data:/data" \
  "$IMAGE"

echo "→ 等待 Bolt 端口(TCP)就绪（最多 120s，不发送凭据，避免触发认证限流）…"
for i in $(seq 1 120); do
  if (exec 3<>/dev/tcp/localhost/"$BOLT_PORT") 2>/dev/null; then
    exec 3>&- 3<&-
    break
  fi
  sleep 1
  if [ "$i" -eq 120 ]; then
    echo "✗ Bolt 端口在 120s 内未就绪，请查看： docker logs $NAME" >&2
    exit 1
  fi
done

echo "→ 执行导入脚本 scripts/init.cypher …"
# 仅在「非认证类」瞬时错误（服务尚未就绪）时重试；一旦是密码/认证错误立即退出，
# 绝不再试 —— 否则会反复发送错误凭据，触发 Neo4j 的 AuthenticationRateLimit 限流。
import_ok=0
for i in $(seq 1 30); do
  if err=$(docker exec -i "$NAME" cypher-shell -u neo4j -p "$PASS" < "$CYPHER" 2>&1); then
    import_ok=1
    break
  fi
  if echo "$err" | grep -qiE 'unauthorized|AuthenticationRateLimit|invalid (username|password)|credentials'; then
    echo "✗ 认证失败（${err}）" >&2
    echo "  请确认 .env 的 NEO4J_PASSWORD 与容器启动密码一致。" >&2
    echo "  （Neo4j 仅在首次启动设置密码；改过密码需先 docker volume rm ${NAME}-data 再重跑。）" >&2
    exit 1
  fi
  sleep 2
done
if [ "$import_ok" -ne 1 ]; then
  echo "✗ 导入失败：$err" >&2
  echo "  请查看： docker logs $NAME" >&2
  exit 1
fi

echo ""
echo "✅ 导入完成！"
echo "   Neo4j Browser： http://localhost:$HTTP_PORT  （neo4j / ${PASS}）"
echo "   Bolt 连接串：   bolt://localhost:$BOLT_PORT"
echo "   后端启用：      设 GOTY_GRAPH_BACKEND=neo4j 且 GOTY_NEO4J_URI=bolt://localhost:$BOLT_PORT"
echo "   停止容器：       docker rm -f $NAME"
echo "   删除数据卷：     docker volume rm ${NAME}-data"
