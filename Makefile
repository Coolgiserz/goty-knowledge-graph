# 年度最佳游戏知识图谱 · 快捷命令
# 常用：
#   make insight      快速启动【洞察模式】（只读：原始数据页 + 原始洞察页）
#   make serve        启动【探索模式】（洞察页 + /explore 探索 SPA）
#   make serve-static 仅静态网站（无需后端）
#   make build        重新生成数据集与网站
#   make run          Docker 运行（默认只读洞察；EXPLORATION=true 可开探索）
# Neo4j（可选图后端）：
#   make neo4j-dev    起开发用 Neo4j（非默认端口）+ 自动导入，供后端可选查询层试跑
#   make neo4j-stop   停止开发用 Neo4j 容器
#   make neo4j-export 重新从 graph.json 导出 CSV
# 工程化：
#   make install      安装依赖（uv sync）+ 安装 pre-commit 钩子
#   make lint         ruff lint + format 检查
#   make test         pytest（正确性 + 安全）
#   make test-perf    性能门禁（pytest -m perf）
#   make ci           本地跑一遍 CI 等价步骤

PY ?= python3
UV ?= uv
PORT ?= 8080
IMG ?= goty-knowledge-graph
EXPLORATION ?= false   # 洞察模式=false（默认，只读）/ 探索模式=true

.PHONY: all build site insight serve serve-static docker run up down neo4j neo4j-dev \
        neo4j-stop neo4j-export analysis install lint test test-perf ci clean help

all: build

## build：重新生成数据集与网站
build: site

site:
	$(PY) src/build.py
	$(PY) src/build_site.py

## insight：快速启动【洞察模式】（只读浏览：原始数据页 + 原始洞察页，http://localhost:8080）
insight:
	GOTY_ENABLE_EXPLORATION=false bash scripts/serve_api.sh $(PORT)

## serve：启动【探索模式】（洞察页 + /explore 探索 SPA，http://localhost:8080）
serve:
	GOTY_ENABLE_EXPLORATION=true bash scripts/serve_api.sh $(PORT)

## serve-static：仅启动原静态图谱网站（无需后端，http://localhost:8080）
serve-static:
	bash scripts/serve.sh $(PORT)

## docker：构建镜像（python + uvicorn，含 API 与原静态站点）
docker:
	docker build -t $(IMG) .

## run：运行镜像（默认只读洞察；EXPLORATION=true 开启探索 SPA，访问 http://localhost:8080）
##   自动加载仓库根 .env（缺失则从 .env.sample 复制）。-e 显式值优先级高于 .env。
run: docker
	@ [ -f .env ] || cp .env.sample .env
	docker run -d -p $(PORT):8080 --name goty-graph --env-file .env -e GOTY_ENABLE_EXPLORATION=$(EXPLORATION) $(IMG)

## up：docker-compose 全栈（网站 + Neo4j 自动导入）
##   自动加载仓库根 .env（缺失则从 .env.sample 复制）。
up:
	@ [ -f .env ] || cp .env.sample .env
	docker-compose up -d --build

## down：停止并移除 compose 服务（保留数据卷用 -v 可加）
down:
	docker-compose down

## neo4j：单独起一个 Neo4j 并自动导入数据集（默认端口 7474/7687）
neo4j:
	bash scripts/neo4j_import.sh

## neo4j-dev：起一个【开发用】Neo4j（非默认端口 7475/7688，容器名 neo4j-goty-dev）并导入
##   用于本地试跑后端可选查询层（GOTY_GRAPH_BACKEND=neo4j），不抢占你本机已有实例。
neo4j-dev:
	bash scripts/neo4j_dev.sh

## neo4j-stop：停止并移除开发用 Neo4j 容器（保留数据卷加 -v 可删）
neo4j-stop:
	docker rm -f neo4j-goty-dev

## neo4j-export：确保 CSV 已从 graph.json 重新导出（经 src/build.py）
neo4j-export:
	$(PY) src/build.py

## analysis：运行数据挖掘/统计机器学习流水线（生成报告与 PNG）
analysis:
	$(UV) run --extra analysis python analysis/run_ml.py

## install：安装依赖（uv sync）+ 安装 pre-commit 钩子
install:
	$(UV) sync --extra analysis
	$(UV) run pre-commit install

## lint：ruff lint + format 检查
lint:
	$(UV) run ruff check api tests
	$(UV) run ruff format --check api tests

## test：pytest（正确性 + 安全测试）
test:
	$(UV) run pytest -q

## test-perf：性能门禁（进程内并发压测，含 p95/吞吐断言）
test-perf:
	$(UV) run pytest -q -m perf

## ci：本地等价执行 CI 流水线（lint + test + perf）
ci: lint test test-perf

## clean：清理生成的站点与打包产物（不删数据）
clean:
	rm -rf site/index.html site/assets/vis-network.min.js

help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //'
