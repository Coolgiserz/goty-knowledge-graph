// 年度最佳游戏知识图谱 · Neo4j 自动导入脚本
// 用法：
//   1) 将本仓库的 data/csv/ 放到 Neo4j 的 import 目录（Docker 中挂载为 /import/csv）
//   2) 执行： cypher-shell -u <user> -p <password> -f scripts/init.cypher
//      或在 Neo4j Browser 中打开本文件全选执行。
// 说明：本脚本假设 CSV 位于 import 目录下的 csv/ 子目录，
//       即路径为 file:///csv/xxx.csv；若直接放在 import 根目录，请把路径改为 file:///xxx.csv。

CREATE CONSTRAINT game_id IF NOT EXISTS FOR (g:Game)   REQUIRE g.game_id IS UNIQUE;
CREATE CONSTRAINT studio_id IF NOT EXISTS FOR (s:Studio) REQUIRE s.studio_id IS UNIQUE;
CREATE CONSTRAINT genre_id IF NOT EXISTS FOR (g:Genre) REQUIRE g.genre_id IS UNIQUE;
CREATE CONSTRAINT award_id IF NOT EXISTS FOR (a:Award) REQUIRE a.award_id IS UNIQUE;

LOAD CSV WITH HEADERS FROM 'file:///csv/games.csv' AS row
CREATE (g:Game { game_id: row.game_id, title: row.title, title_zh: row.title_zh,
  year: toInteger(row.year), is_goty: row.is_goty = 'True',
  genre: row.genre, developer: row.developer, publisher: row.publisher,
  platforms: row.platforms, player_rating: toInteger(row.player_rating),
  description: row.description });

LOAD CSV WITH HEADERS FROM 'file:///csv/studios.csv' AS row
CREATE (s:Studio { studio_id: row.studio_id, name: row.name, name_zh: row.name_zh,
  founded: toInteger(row.founded), country: row.country, hq: row.hq, parent: row.parent });

LOAD CSV WITH HEADERS FROM 'file:///csv/genres.csv' AS row
CREATE (g:Genre { genre_id: row.genre_id, name: row.name, parent: row.parent, tier: toInteger(row.tier) });

LOAD CSV WITH HEADERS FROM 'file:///csv/awards.csv' AS row
CREATE (a:Award { award_id: row.award_id, game_id: row.game_id, name: row.name,
  year: toInteger(row.year), body: row.body });

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_developed.csv' AS row
MATCH (s:Studio {studio_id: row.studio_id}), (g:Game {game_id: row.game_id})
CREATE (s)-[:DEVELOPED]->(g);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_won.csv' AS row
MATCH (g:Game {game_id: row.game_id}), (a:Award {award_id: row.award_id})
CREATE (g)-[:WON]->(a);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_genre.csv' AS row
MATCH (g:Game {game_id: row.game_id}), (gn:Genre {genre_id: row.genre_id})
CREATE (g)-[:BELONGS_TO_GENRE]->(gn);

LOAD CSV WITH HEADERS FROM 'file:///csv/rel_subclass.csv' AS row
MATCH (c:Genre {genre_id: row.child_id}), (p:Genre {genre_id: row.parent_id})
CREATE (c)-[:SUBCLASS_OF]->(p);
