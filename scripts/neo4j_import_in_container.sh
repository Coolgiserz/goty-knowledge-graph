#!/usr/bin/env bash
# 容器内一次性导入脚本（由 docker-compose 的 importer 服务调用）。
# 设计要点：
#   - 通过 TCP 等待 Bolt 端口就绪，期间「不发送任何凭据」，避免触发 Neo4j 防暴破限流。
#   - 执行 scripts/init.cypher；init.cypher 本身已幂等（MERGE），重跑不会产生重复节点。
#   - 成功即打印节点数并 exit 0；若因其它原因被重启（restart 策略），重跑也安全。
set -uo pipefail

PW="${NEO4J_PASSWORD:-neo4j}"
BOLT_HOST="${NEO4J_HOST:-neo4j}"
BOLT_PORT="${NEO4J_BOLT_PORT:-7687}"
CYPHER="/init.cypher"
MAX_WAIT=60
MAX_TRIES=30

echo "importer: 等待 Neo4j Bolt 端口($BOLT_HOST:$BOLT_PORT)就绪（不发送凭据，避免认证限流）…"
ready=0
for _ in $(seq 1 "$MAX_WAIT"); do
  if (exec 3<>"/dev/tcp/$BOLT_HOST/$BOLT_PORT") 2>/dev/null; then
    exec 3>&- 3<&-
    ready=1
    break
  fi
  sleep 2
done
if [ "$ready" -ne 1 ]; then
  echo "importer: ✗ Bolt 端口在等待期内未就绪，请查看： docker logs ${BOLT_HOST}" >&2
  exit 1
fi

echo "importer: 执行 $CYPHER …"
imported=0
last_err=""
for _ in $(seq 1 "$MAX_TRIES"); do
  # 捕获 stdout+stderr：成功时 err 为空，失败时 err 含错误信息
  if last_err=$(cypher-shell -a "bolt://$BOLT_HOST:$BOLT_PORT" -u neo4j -p "$PW" -f "$CYPHER" 2>&1); then
    imported=1
    break
  fi
  # 一旦是认证类错误，立即退出，绝不再试 —— 否则会反复发送错误凭据触发限流
  if echo "$last_err" | grep -qiE 'unauthorized|AuthenticationRateLimit|invalid (username|password)|credentials'; then
    echo "importer: ✗ 认证失败 —— $last_err" >&2
    echo "importer: 请确认 NEO4J_PASSWORD 与 neo4j 服务密码一致（改密码需 docker compose down -v 重建）。" >&2
    exit 1
  fi
  sleep 2
done

if [ "$imported" -ne 1 ]; then
  echo "importer: ✗ 导入失败（重试耗尽）：$last_err" >&2
  exit 1
fi

# 校验：读取节点总数，确认数据已写入（此查询失败不阻断导入结果）
cnt=$(cypher-shell -a "bolt://$BOLT_HOST:$BOLT_PORT" -u neo4j -p "$PW" -c "MATCH (n) RETURN count(n)" 2>/dev/null | tail -n 1 | tr -d '\r')
echo "importer: 导入完成 ✅（节点数：${cnt:-未知}）"
exit 0
