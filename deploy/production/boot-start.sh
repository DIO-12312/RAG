#!/usr/bin/env bash
# 主机重启后按已发布记录恢复生产栈。
#
# 使用场景：非计划重启（OOM、内核升级、云厂商维护）后，容器不会自动回到运行态，
# 而 `make production-run` 会重新构建镜像、`make production-deploy` 需要 GitHub 触发。
# 本脚本只做「恢复」：读取 /var/lib/rag-deploy/active.json 指向的已渲染 Compose 配置，
# 按依赖顺序把服务拉起来，不构建、不迁移、不删除任何卷，也不修改发布记录。
#
# 用法：deploy/production/boot-start.sh
#   RAG_DEPLOY_STATE  发布状态目录，默认 /var/lib/rag-deploy
#   RAG_DEPLOY_PROJECT Compose 项目名，默认 rag-production
set -euo pipefail

STATE="${RAG_DEPLOY_STATE:-/var/lib/rag-deploy}"
PROJECT="${RAG_DEPLOY_PROJECT:-rag-production}"

if [ ! -f "$STATE/active.json" ]; then
    echo "[boot-start] 未找到发布记录 $STATE/active.json" >&2
    exit 1
fi
CONFIG="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["config"])' "$STATE/active.json")"
if [ ! -f "$CONFIG" ]; then
    echo "[boot-start] 发布记录指向的 Compose 配置不存在：$CONFIG" >&2
    exit 1
fi
echo "[boot-start] 使用发布记录 $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["sha"][:7], "seq", d["sequence"])' "$STATE/active.json")"

compose() { docker compose --project-name "$PROJECT" -f "$CONFIG" "$@"; }

echo "[boot-start] 1/5 基础设施"
observability_services=()
for service in otel-collector prometheus tempo; do
    if compose config --services | grep -qx "$service"; then
        observability_services+=("$service")
    fi
done
compose up -d --no-build --pull never --wait --wait-timeout 300 \
    elasticsearch nats rag-mysql product-mysql "${observability_services[@]}"

echo "[boot-start] 2/5 一次性初始化（Search Guard 材料 / Alembic 迁移）"
compose up -d --no-build --pull never --wait --wait-timeout 300 \
    rag-search-guard-bootstrap rag-migrate

echo "[boot-start] 3/5 摄取与检索服务"
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 180 \
    rag-server rag-worker rag-outbox

echo "[boot-start] 4/5 产品 API 与前端"
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 180 api web

echo "[boot-start] 5/5 公网边缘"
compose up -d --no-build --pull never --no-deps --wait --wait-timeout 120 caddy

compose exec -T api wget -q -O - http://127.0.0.1:8080/readyz >/dev/null
echo "[boot-start] 生产栈已恢复"
