# 年度最佳游戏知识图谱 · 快捷命令
# 常用：
#   make insight      快速启动【洞察模式】（只读：原始数据页 + 原始洞察页）
#   make serve        启动【探索模式】（洞察页 + /explore 探索 SPA）
#   make serve-static 仅静态网站（无需后端）
#   make build        重新生成数据集与网站
#   make run          Docker 运行（默认只读洞察；EXPLORATION=true 可开探索）
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

.PHONY: all build site insight serve serve-static docker run up down neo4j analysis \
        install lint test test-perf ci clean help

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
run: docker
	docker run -d -p $(PORT):8080 --name goty-graph -e GOTY_ENABLE_EXPLORATION=$(EXPLORATION) $(IMG)

## up：docker-compose 全栈（网站 + Neo4j 自动导入）
up:
	docker-compose up -d --build

## down：停止并移除 compose 服务（保留数据卷用 -v 可加）
down:
	docker-compose down

## neo4j：单独起一个 Neo4j 并自动导入数据集
neo4j:
	bash scripts/neo4j_import.sh

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
