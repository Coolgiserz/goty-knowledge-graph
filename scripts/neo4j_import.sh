#!/usr/bin/env bash
# 单独启动一个 Neo4j 容器并自动导入本数据集（等价于 docker-compose 的 neo4j+importer）。
# 用法： bash scripts/neo4j_import.sh
# 完成后访问 http://localhost:7474  （用户名 neo4j / 密码 password123）
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CSV_DIR="$ROOT_DIR/data/csv"
CYPHER="$ROOT_DIR/scripts/init.cypher"
NAME="neo4j-goty"
# 若仓库根 .env 设了 NEO4J_PASSWORD，则沿用；否则用默认弱密码（仅本地演示）
PASS="password123"
if [ -f "$ROOT_DIR/.env" ] && grep -qE '^NEO4J_PASSWORD=' "$ROOT_DIR/.env"; then
  PASS="$(grep -E '^NEO4J_PASSWORD=' "$ROOT_DIR/.env" | head -1 | cut -d= -f2-)"
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "✗ 未检测到 docker，请先安装 Docker：https://docs.docker.com/get-docker/" >&2
  exit 1
fi

# 若已存在同名容器则先移除
if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
  echo "→ 发现已有容器 $NAME，正在移除…"
  docker rm -f "$NAME" >/dev/null
fi

echo "→ 启动 Neo4j 容器（数据卷 neo4j-goty-data）…"
docker run -d --name "$NAME" \
  -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH="neo4j/$PASS" \
  -v "$CSV_DIR:/import/csv:ro" \
  -v neo4j-goty-data:/data \
  neo4j:5.26-community

echo "→ 等待 Neo4j 就绪（最多 120s）…"
for i in $(seq 1 120); do
  if docker exec "$NAME" cypher-shell -u neo4j -p "$PASS" "RETURN 1;" >/dev/null 2>&1; then
    break
  fi
  sleep 1
  if [ "$i" -eq 120 ]; then
    echo "✗ Neo4j 启动超时，请查看： docker logs $NAME" >&2
    exit 1
  fi
done

echo "→ 执行导入脚本 scripts/init.cypher …"
docker exec -i "$NAME" cypher-shell -u neo4j -p "$PASS" < "$CYPHER"

echo ""
echo "✅ 导入完成！"
echo "   网站(若用 docker-compose)： http://localhost:8080"
echo "   Neo4j Browser：            http://localhost:7474  （neo4j / $PASS）"
echo "   停止容器： docker stop $NAME"
echo "   删除容器与数据： docker rm -f $NAME && docker volume rm neo4j-goty-data"
