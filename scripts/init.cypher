// 年度最佳游戏知识图谱 · Neo4j 自动导入脚本（幂等：可重复执行，不产生重复节点/关系）
// 用法：
//   1) 将本仓库的 data/csv/ 放到 Neo4j 的 import 目录（Docker 中挂载为 /import/csv）
//   2) 执行： cypher-shell -u <user> -p <password> -f scripts/init.cypher
//      或在 Neo4j Browser 中打开本文件全选执行。
// 说明：本脚本假设 CSV 位于 import 目录下的 csv/ 子目录，
//       即路径为 file:///csv/xxx.csv；若直接放在 import 根目录，请把路径改为 file:///xxx.csv。

// 唯一性约束（IF NOT EXISTS 本身幂等，重复执行无副作用）
CREATE CONSTRAINT game_id IF NOT EXISTS FOR (g:Game)   REQUIRE g.game_id IS UNIQUE;
CREATE CONSTRAINT studio_id IF NOT EXISTS FOR (s:Studio) REQUIRE s.studio_id IS UNIQUE;
CREATE CONSTRAINT genre_id IF NOT EXISTS FOR (g:Genre) REQUIRE g.genre_id IS UNIQUE;
CREATE CONSTRAINT award_id IF NOT EXISTS FOR (a:Award) REQUIRE a.award_id IS UNIQUE;

// 节点：用 MERGE 按唯一键，重复执行只更新属性、不新增节点
LOAD CSV WITH HEADERS FROM 'file:///csv/games.csv' AS row
MERGE (g:Game { game_id: row.game_id })
SET g.title = row.title, g.title_zh = row.title_zh,
    g.year = toInteger(row.year), g.is_goty = (row.is_goty = 'True'),
    g.genre = row.genre, g.developer = row.developer, g.publisher = row.publisher,
    g.platforms = row.platforms, g.player_rating = toInteger(row.player_rating),
    g.description = row.description;

LOAD CSV WITH HEADERS FROM 'file:///csv/studios.csv' AS row
MERGE (s:Studio { studio_id: row.studio_id })
SET s.name = row.name, s.name_zh = row.name_zh,
    s.founded = toInteger(row.founded), s.country = row.country,
    s.hq = row.hq, s.parent = row.parent;

LOAD CSV WITH HEADERS FROM 'file:///csv/genres.csv' AS row
MERGE (g:Genre { genre_id: row.genre_id })
SET g.name = row.name, g.parent = row.parent, g.tier = toInteger(row.tier);

LOAD CSV WITH HEADERS FROM 'file:///csv/awards.csv' AS row
MERGE (a:Award { award_id: row.award_id })
SET a.game_id = row.game_id, a.name = row.name,
    a.year = toInteger(row.year), a.body = row.body;

// 关系：先 MATCH 两端（已存在），再 MERGE 关系，重复执行不会重复建边
LOAD CSV WITH HEADERS FROM 'file:///csv/rel_developed.csv' AS row
MATCH (s:Studio {studio_id: row.studio_id}), (g:Game {game_id: row.game_id})
MERGE (s)-[:DEVELOPED]->(g);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_won.csv' AS row
MATCH (g:Game {game_id: row.game_id}), (a:Award {award_id: row.award_id})
MERGE (g)-[:WON]->(a);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_genre.csv' AS row
MATCH (g:Game {game_id: row.game_id}), (gn:Genre {genre_id: row.genre_id})
MERGE (g)-[:BELONGS_TO_GENRE]->(gn);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_subclass.csv' AS row
MATCH (c:Genre {genre_id: row.child_id}), (p:Genre {genre_id: row.parent_id})
MERGE (c)-[:SUBCLASS_OF]->(p);
