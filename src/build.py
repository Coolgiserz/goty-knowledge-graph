#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge raw GOTY research JSON into a knowledge-graph dataset.

Outputs (relative to repo root):
  data/graph.json            - merged graph (for inspection / website)
  data/csv/*.csv             - plain-header CSVs (for Cypher LOAD CSV)
  data/neo4j/*.csv           - colon-header CSVs (for neo4j-admin import)
  docs/neo4j_tutorial.md     - import tutorial (zh)

Run from anywhere:
  python3 src/build.py
"""
import json, csv, os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # repo root (src/..)
RAW = os.path.join(ROOT, "data/raw")
DATA_CSV = os.path.join(ROOT, "data/csv")
DATA_NEO = os.path.join(ROOT, "data/neo4j")
os.makedirs(DATA_CSV, exist_ok=True)
os.makedirs(DATA_NEO, exist_ok=True)

# ---------- 1. load & merge ----------
studios = {}
goty = []
for i in range(1, 6):
    with open(os.path.join(RAW, f"agent{i}.json"), encoding="utf-8") as f:
        d = json.load(f)
    for s in d["studios"]:
        studios[s["id"]] = s
    goty.extend(d["goty_games"])

# ---------- 2. build game nodes ----------
games = {}            # gid -> record
title2gid = {}
genre_ids = {}        # name -> gid

def add_game(rec):
    gid = f"game_{len(games)+1:03d}"
    games[gid] = rec
    title2gid[rec["title"]] = gid
    return gid

# GOTY first
for g in goty:
    body = "The Game Awards" if g["award_year"] >= 2014 else "Spike VGA / VGX"
    add_game({
        "title": g["title"], "title_zh": g["title_zh"], "year": g["award_year"],
        "is_goty": True, "genre": g["genre"], "developer": g["developer"],
        "developer_id": g["developer_id"], "publisher": g.get("publisher", ""),
        "platforms": g.get("platforms", ""), "release_date": g.get("release_date", ""),
        "player_rating": g.get("player_rating", ""), "rating_source": g.get("rating_source", ""),
        "gameplay": g.get("gameplay", ""), "unique_features": g.get("unique_features", ""),
        "drawbacks": g.get("drawbacks", ""), "awards": g.get("awards", ""),
        "influence": g.get("influence", ""), "description": g.get("description", ""),
        "body": body,
    })

# other games (studio's non-GOTY titles)
for sid, s in studios.items():
    for og in s.get("other_games", []):
        if og["title"] in title2gid:
            continue
        add_game({
            "title": og["title"], "title_zh": og.get("title_zh", og["title"]),
            "year": og.get("year", ""), "is_goty": False, "genre": og.get("genre", ""),
            "developer": s["name"], "developer_id": sid, "publisher": "",
            "platforms": "", "release_date": "",
            "player_rating": og.get("rating", ""), "rating_source": og.get("rating_source", ""),
            "gameplay": "", "unique_features": "", "drawbacks": "",
            "awards": "", "influence": "", "description": og.get("note", ""), "body": "",
        })

# ---------- 3. genres & awards ----------
awards = {}
rel_developed = []
rel_won = []
rel_genre = []

for gid, rec in games.items():
    rel_developed.append((rec["developer_id"], gid))
    gn = rec["genre"].strip()
    if gn:
        if gn not in genre_ids:
            genre_ids[gn] = f"genre_{len(genre_ids)+1:03d}"
        rel_genre.append((gid, genre_ids[gn]))
    if rec["is_goty"]:
        aid = f"award_{gid}"
        awards[aid] = {"game_id": gid, "name": "年度最佳游戏 (Game of the Year)",
                       "year": rec["year"], "body": rec["body"]}
        rel_won.append((gid, aid))

# ---------- 4. helper writers ----------
def w_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)

# plain-header CSVs (for Cypher LOAD CSV / inspection)
w_csv(os.path.join(DATA_CSV, "games.csv"),
      ["game_id","title","title_zh","year","is_goty","genre","developer","developer_id",
       "publisher","platforms","release_date","player_rating","rating_source",
       "gameplay","unique_features","drawbacks","awards","influence","description"],
      [[gid, r["title"], r["title_zh"], r["year"], r["is_goty"], r["genre"], r["developer"],
        r["developer_id"], r["publisher"], r["platforms"], r["release_date"], r["player_rating"],
        r["rating_source"], r["gameplay"], r["unique_features"], r["drawbacks"], r["awards"],
        r["influence"], r["description"]] for gid, r in games.items()])

w_csv(os.path.join(DATA_CSV, "studios.csv"),
      ["studio_id","name","name_zh","founded","country","hq","parent","description"],
      [[s["id"], s["name"], s["name_zh"], s["founded"], s["country"], s["hq"],
        s["parent"], s["description"]] for s in studios.values()])

w_csv(os.path.join(DATA_CSV, "genres.csv"), ["genre_id","name"],
      [[gid, name] for name, gid in genre_ids.items()])

w_csv(os.path.join(DATA_CSV, "awards.csv"), ["award_id","game_id","name","year","body"],
      [[aid, a["game_id"], a["name"], a["year"], a["body"]] for aid, a in awards.items()])

w_csv(os.path.join(DATA_CSV, "rel_developed.csv"), ["studio_id","game_id"], rel_developed)
w_csv(os.path.join(DATA_CSV, "rel_won.csv"), ["game_id","award_id"], rel_won)
w_csv(os.path.join(DATA_CSV, "rel_genre.csv"), ["game_id","genre_id"], rel_genre)

# colon-header CSVs (for neo4j-admin import)
w_csv(os.path.join(DATA_NEO, "games.csv"),
      ["game_id:ID(Game)","title:string","title_zh:string","year:int","is_goty:boolean",
       "genre:string","developer:string","developer_id:string","publisher:string",
       "platforms:string","release_date:string","player_rating:int","rating_source:string",
       "gameplay:string","unique_features:string","drawbacks:string","awards:string",
       "influence:string","description:string"],
      [[gid, r["title"], r["title_zh"], r["year"], ("true" if r["is_goty"] else "false"), r["genre"], r["developer"],
        r["developer_id"], r["publisher"], r["platforms"], r["release_date"], r["player_rating"],
        r["rating_source"], r["gameplay"], r["unique_features"], r["drawbacks"], r["awards"],
        r["influence"], r["description"]] for gid, r in games.items()])

w_csv(os.path.join(DATA_NEO, "studios.csv"),
      ["studio_id:ID(Studio)","name:string","name_zh:string","founded:int","country:string",
       "hq:string","parent:string","description:string"],
      [[s["id"], s["name"], s["name_zh"], s["founded"], s["country"], s["hq"],
        s["parent"], s["description"]] for s in studios.values()])

w_csv(os.path.join(DATA_NEO, "genres.csv"), ["genre_id:ID(Genre)","name:string"],
      [[gid, name] for name, gid in genre_ids.items()])

w_csv(os.path.join(DATA_NEO, "awards.csv"),
      ["award_id:ID(Award)","game_id:string","name:string","year:int","body:string"],
      [[aid, a["game_id"], a["name"], a["year"], a["body"]] for aid, a in awards.items()])

w_csv(os.path.join(DATA_NEO, "rel_developed.csv"), [":START_ID(Studio)",":END_ID(Game)",":TYPE"],
      [[a, b, "DEVELOPED"] for a, b in rel_developed])
w_csv(os.path.join(DATA_NEO, "rel_won.csv"), [":START_ID(Game)",":END_ID(Award)",":TYPE"],
      [[a, b, "WON"] for a, b in rel_won])
w_csv(os.path.join(DATA_NEO, "rel_genre.csv"), [":START_ID(Game)",":END_ID(Genre)",":TYPE"],
      [[a, b, "BELONGS_TO_GENRE"] for a, b in rel_genre])

# ---------- 5. graph.json for website ----------
nodes = []
edges = []
for gid, r in games.items():
    nodes.append({
        "id": gid, "group": "goty" if r["is_goty"] else "game",
        "label": f'{r["title_zh"]} ({r["year"]})', "raw": r,
    })
for sid, s in studios.items():
    nodes.append({"id": sid, "group": "studio",
                  "label": s["name_zh"], "raw": s})
for name, gid in genre_ids.items():
    nodes.append({"id": gid, "group": "genre", "label": name, "raw": {"name": name}})
for aid, a in awards.items():
    nodes.append({"id": aid, "group": "award",
                  "label": f'GOTY {a["year"]}', "raw": a})
for a, b in rel_developed:
    edges.append({"from": a, "to": b, "type": "DEVELOPED"})
for a, b in rel_won:
    edges.append({"from": a, "to": b, "type": "WON"})
for a, b in rel_genre:
    edges.append({"from": a, "to": b, "type": "BELONGS_TO_GENRE"})

GRAPH = {
    "nodes": nodes, "edges": edges,
    "stats": {
        "goty": sum(1 for r in games.values() if r["is_goty"]),
        "games": len(games), "studios": len(studios),
        "genres": len(genre_ids), "awards": len(awards),
    },
}
with open(os.path.join(ROOT, "data/graph.json"), "w", encoding="utf-8") as f:
    json.dump(GRAPH, f, ensure_ascii=False, indent=1)

print("Games:", len(games), "Studios:", len(studios),
      "Genres:", len(genre_ids), "Awards:", len(awards),
      "Edges:", len(edges))

# ---------- 6. tutorial ----------
tut = f"""# 年度最佳游戏知识图谱 · Neo4j 导入教程

本数据集覆盖 **近 20 年（2006–2025）** 的年度最佳游戏（Game of the Year），来源为 Spike VGA / VGX（2006–2013）与 The Game Awards（2014–2025，含 2025 年由 Sandfall Interactive 开发的《光与影：33 号远征队》）。共包含：

- **{GRAPH['stats']['goty']}** 款年度最佳游戏（GOTY）
- **{GRAPH['stats']['games']}** 款游戏节点（含各开发商的其他代表作）
- **{GRAPH['stats']['studios']}** 家开发商
- **{GRAPH['stats']['genres']}** 个游戏类型
- **{GRAPH['stats']['awards']}** 个年度大奖节点

## 节点与关系模型

| 标签 | 说明 | 关键属性 |
|------|------|----------|
| `Game` | 游戏（含 GOTY 与开发商其他作品） | title, title_zh, year, is_goty, genre, developer, publisher, platforms, player_rating, gameplay, unique_features, drawbacks, awards, influence, description |
| `Studio` | 开发商 | name, name_zh, founded, country, hq, parent, description |
| `Genre` | 游戏类型 | name |
| `Award` | 年度最佳游戏奖 | name, year, body |

关系：
- `(:Studio)-[:DEVELOPED]->(:Game)` — 开发商开发了某游戏（含 GOTY 与“其他作品”）
- `(:Game)-[:WON]->(:Award)` — 该游戏获得年度最佳（仅 GOTY 有）
- `(:Game)-[:BELONGS_TO_GENRE]->(:Genre)` — 游戏所属类型

## 数据集文件

`data/neo4j/` 下为 **带冒号表头** 的 CSV（专供 `neo4j-admin import`）：

```
data/neo4j/
├── games.csv          (:ID(Game) ...)
├── studios.csv        (:ID(Studio) ...)
├── genres.csv         (:ID(Genre) ...)
├── awards.csv         (:ID(Award) ...)
├── rel_developed.csv  :START_ID(Studio),:END_ID(Game),:TYPE
├── rel_won.csv        :START_ID(Game),:END_ID(Award),:TYPE
└── rel_genre.csv      :START_ID(Game),:END_ID(Genre),:TYPE
```

`data/csv/` 下为 **普通表头** 的 CSV（供 Cypher `LOAD CSV` 使用，字段名不含冒号），并配套 `scripts/init.cypher` 一键导入脚本。

---

## 方式 A：neo4j-admin import（离线、适合全新数据库）

> 适用于为新库一次性批量导入。需 **停止** 目标 Neo4j 实例，导入后启动。Neo4j 5.x 语法如下。

将 `data/neo4j/` 整个目录放到 Neo4j 可访问的路径（例如 `/var/lib/neo4j/import/goty/`），执行：

```bash
neo4j-admin database import full goty \\
  --nodes=Game=/path/to/data/neo4j/games.csv \\
  --nodes=Studio=/path/to/data/neo4j/studios.csv \\
  --nodes=Genre=/path/to/data/neo4j/genres.csv \\
  --nodes=Award=/path/to/data/neo4j/awards.csv \\
  --relationships=/path/to/data/neo4j/rel_developed.csv \\
  --relationships=/path/to/data/neo4j/rel_won.csv \\
  --relationships=/path/to/data/neo4j/rel_genre.csv \\
  --delimiter="," --quote='"' --multiline=true
```

导入后启动数据库并设置默认库为 `goty`（在 `neo4j.conf` 中 `dbms.default_database=goty`），再 `neo4j start`。

> 提示：若使用 Docker / Neo4j Desktop，可把 `data/neo4j` 挂载为 `/import/goty` 卷，路径写成 `/import/goty/...`。

---

## 方式 B：Cypher LOAD CSV（在线、适合已有实例）

适用于数据库已在运行、不想停机导入的场景。把 `data/csv/` 放到 Neo4j 的 `import` 目录（或开启 `dbms.security.allow_csv_import_from_url` 后使用 `file:///` / http URL），然后执行 `scripts/init.cypher`：

```bash
# 将 CSV 放到 Neo4j 的 import 目录后，直接运行：
cypher-shell -u <user> -p <password> -f scripts/init.cypher

# 或用 Neo4j Browser 打开 scripts/init.cypher 全选执行
```

`scripts/init.cypher` 内容（CSV 假定挂载在 `import/csv/`）：

```cypher
CREATE CONSTRAINT game_id IF NOT EXISTS FOR (g:Game)   REQUIRE g.game_id IS UNIQUE;
CREATE CONSTRAINT studio_id IF NOT EXISTS FOR (s:Studio) REQUIRE s.studio_id IS UNIQUE;
CREATE CONSTRAINT genre_id IF NOT EXISTS FOR (g:Genre) REQUIRE g.genre_id IS UNIQUE;
CREATE CONSTRAINT award_id IF NOT EXISTS FOR (a:Award) REQUIRE a.award_id IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///csv/games.csv' AS row
CREATE (g:Game {{ game_id: row.game_id, title: row.title, title_zh: row.title_zh,
  year: toInteger(row.year), is_goty: row.is_goty = 'True',
  genre: row.genre, developer: row.developer, publisher: row.publisher,
  platforms: row.platforms, player_rating: toInteger(row.player_rating),
  description: row.description }});

LOAD CSV WITH HEADERS FROM 'file:///csv/studios.csv' AS row
CREATE (s:Studio {{ studio_id: row.studio_id, name: row.name, name_zh: row.name_zh,
  founded: toInteger(row.founded), country: row.country, hq: row.hq, parent: row.parent }});

LOAD CSV WITH HEADERS FROM 'file:///csv/genres.csv' AS row
CREATE (g:Genre {{ genre_id: row.genre_id, name: row.name }});

LOAD CSV WITH HEADERS FROM 'file:///csv/awards.csv' AS row
CREATE (a:Award {{ award_id: row.award_id, game_id: row.game_id, name: row.name,
  year: toInteger(row.year), body: row.body }});

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_developed.csv' AS row
MATCH (s:Studio {{studio_id: row.studio_id}}), (g:Game {{game_id: row.game_id}})
CREATE (s)-[:DEVELOPED]->(g);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_won.csv' AS row
MATCH (g:Game {{game_id: row.game_id}}), (a:Award {{award_id: row.award_id}})
CREATE (g)-[:WON]->(a);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_genre.csv' AS row
MATCH (g:Game {{game_id: row.game_id}}), (gn:Genre {{genre_id: row.genre_id}})
CREATE (g)-[:BELONGS_TO_GENRE]->(gn);
```

---

## 验证与示例查询

```cypher
// 节点 / 关系总数
MATCH (n) RETURN count(n) AS nodes;
MATCH ()-[r]->() RETURN type(r) AS rel, count(*) AS cnt;

// 所有年度最佳游戏及其开发商
MATCH (s:Studio)-[:DEVELOPED]->(g:Game)-[:WON]->(a:Award)
RETURN a.year AS 年份, g.title_zh AS 游戏, s.name_zh AS 开发商
ORDER BY a.year;

// 某开发商（如 FromSoftware）的全部作品
MATCH (s:Studio {{studio_id:'studio_fromsoftware'}})-[:DEVELOPED]->(g:Game)
RETURN g.title_zh AS 作品, g.year AS 年份, g.is_goty AS 是否年度最佳
ORDER BY g.year;

// 年度最佳游戏之间的“同开发商”关系（哪些工作室多次夺冠）
MATCH (s:Studio)-[:DEVELOPED]->(g:Game)-[:WON]->(:Award)
WITH s, count(g) AS wins WHERE wins > 1
RETURN s.name_zh AS 工作室, wins AS 夺冠次数;
```

---

## 用本项目自带的 Docker 一键起库（推荐）

项目根目录提供了 `docker-compose.yml`，`web` 服务托管知识图谱网站，`neo4j` + `importer` 服务会自动把 `data/csv/` 导入到一个全新的 Neo4j 实例：

```bash
docker-compose up -d
# 网站：http://localhost:8080
# Neo4j Browser：http://localhost:7474  （用户名 neo4j / 密码 password123）
```

`importer` 服务在 Neo4j 健康检查通过后自动执行 `scripts/init.cypher`（LOAD CSV）。
"""
with open(os.path.join(ROOT, "docs/neo4j_tutorial.md"), "w", encoding="utf-8") as f:
    f.write(tut)
print("tutorial written -> docs/neo4j_tutorial.md")
