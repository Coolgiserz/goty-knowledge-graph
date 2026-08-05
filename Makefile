# 年度最佳游戏知识图谱 · 快捷命令
# 常用： make build  /  make serve  /  make up  /  make neo4j

PY ?= python3
PORT ?= 8080
IMG ?= goty-knowledge-graph

.PHONY: all build site serve docker run up down neo4j clean help

all: build

## build：重新生成数据集与网站
build: site

site:
	$(PY) src/build.py
	$(PY) src/build_site.py

## serve：本地启动静态网站（http://localhost:8080）
serve:
	bash scripts/serve.sh $(PORT)

## docker：仅构建网站镜像
docker:
	docker build -t $(IMG) .

## run：运行网站镜像
run: docker
	docker run -d -p $(PORT):80 --name goty-graph $(IMG)

## up：docker-compose 全栈（网站 + Neo4j 自动导入）
up:
	docker-compose up -d --build

## down：停止并移除 compose 服务（保留数据卷用 -v 可加）
down:
	docker-compose down

## neo4j：单独起一个 Neo4j 并自动导入数据集
neo4j:
	bash scripts/neo4j_import.sh

## clean：清理生成的站点与打包产物（不删数据）
clean:
	rm -rf site/index.html site/assets/vis-network.min.js

help:
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/## //'
