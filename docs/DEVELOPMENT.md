# 开发与数据扩展指南

本文档说明如何重新生成数据集与网站、如何修正或扩展数据、以及如何修改构建流程。

## 1. 环境要求

- Python 3.8+（仅用于构建，网站本身无需 Python）
- 无需 Node / 前端构建工具（vis-network 已作为 `vendor/vis-network.min.js` 本地化）

## 2. 构建流程

数据从 `data/raw/*.json` 出发，经过两步生成全部产物：

```
data/raw/agent1~5.json
        │  src/build.py
        ▼
data/graph.json        合并后的图谱（供网站/检查）
data/csv/*.csv         普通表头 CSV（LOAD CSV）
data/neo4j/*.csv       冒号表头 CSV（neo4j-admin import）
docs/neo4j_tutorial.md 导入教程
        │  src/build_site.py
        ▼
site/index.html        交互式网站（数据已内联）
site/assets/vis-network.min.js
```

重新生成全部产物：

```bash
make build
# 等价于：
python3 src/build.py
python3 src/build_site.py
```

## 3. 修正 / 补充数据

所有「事实数据」都集中在 `data/raw/` 下的 5 个 JSON 文件，按工作室分簇：

- `agent1.json`：Bethesda / Rockstar / Irrational
- `agent2.json`：Naughty Dog / Santa Monica / Team Asobi
- `agent3.json`：Hazelight / BioWare / CD Projekt Red
- `agent4.json`：Larian / Telltale / Sandfall
- `agent5.json`：FromSoftware / Nintendo EPD / Blizzard

每个文件结构：

```json
{
  "studios": [
    { "id": "studio_xxx", "name": "...", "name_zh": "...", "founded": 1995,
      "country": "美国", "hq": "...", "parent": "", "description": "...",
      "other_games": [ {"title":"...","title_zh":"...","year":2008,"genre":"...","note":"..."} ] }
  ],
  "goty_games": [
    { "award_year": 2011, "title": "The Elder Scrolls V: Skyrim", "title_zh": "上古卷轴5：天际",
      "genre": "开放世界 RPG", "developer": "Bethesda Game Studios", "developer_id": "studio_bethesda",
      "publisher": "Bethesda Softworks", "platforms": "PC;PS3;Xbox 360",
      "release_date": "2011-11-11", "player_rating": 96, "rating_source": "Metacritic",
      "gameplay": "...", "unique_features": "...", "drawbacks": "...",
      "awards": "...", "influence": "...", "description": "..." }
  ]
}
```

- **改某款游戏属性**：直接编辑对应 `goty_games` 条目。
- **新增 GOTY**：在对应 agent 的 `goty_games` 追加，并把其开发商加/补到 `studios`（含 `other_games`）。
- **新增非 GOTY 作品**：在对应工作室的 `other_games` 中追加。
- **新增一家开发商**：在任一 agent 的 `studios` 追加，并尽量带几条 `other_games`。

> 注意：GOTY 的 `developer_id` 必须与其工作室在 `studios` 中的 `id` 完全一致，否则图谱中「工作室→作品」关系会断开。

改完之后：

```bash
make build
```

## 4. 修改构建脚本

- `src/build.py`：负责合并、生成 CSV 与 `graph.json`、写教程。输出路径均为相对于仓库根目录，已无硬编码绝对路径。
- `src/build_site.py`：把 `data/graph.json` 内联进 `site/index.html`，并复制 `vendor/vis-network.min.js` 到 `site/assets/`。网站是**纯静态、数据内联**，因此可离线双击打开，也可被任意静态服务器 / nginx 托管。

## 5. 更新前端依赖（vis-network）

```bash
curl -sSL -o vendor/vis-network.min.js \
  https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js
make build   # 重新复制进 site/assets/
```

## 6. 部署相关

- **仅网站**：`make docker && make run`（nginx 镜像，端口 8080）。
- **全栈**：`make up`（docker-compose：web + neo4j + importer 自动导入）。
- **自定义 Neo4j**：把 `data/csv/` 放到其 `import` 目录，执行 `scripts/init.cypher`；或 `make neo4j` 单独起一个本地 Neo4j 并自动导入。
- 修改网站容器配置见 `docker/nginx.conf`；修改 compose 编排见 `docker-compose.yml`。
