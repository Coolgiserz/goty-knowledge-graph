#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build site/index.html (knowledge-graph explorer) from data/graph.json.

Run from anywhere:
  python3 src/build_site.py
"""
import json, os, re, shutil, html

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # repo root (src/..)
GRAPH_PATH = os.path.join(ROOT, "data/graph.json")
SITE_DIR = os.path.join(ROOT, "site")
ASSET_DIR = os.path.join(SITE_DIR, "assets")
VENDOR_JS = os.path.join(ROOT, "vendor/vis-network.min.js")

with open(GRAPH_PATH, encoding="utf-8") as f:
    G = json.load(f)

# enrich nodes with shape/size for vis-network
SHAPE = {"goty": "star", "game": "dot", "studio": "diamond", "genre": "hexagon", "award": "triangle"}
SIZE = {"goty": 20, "game": 11, "studio": 22, "genre": 11, "award": 14}
for n in G["nodes"]:
    n["shape"] = SHAPE[n["group"]]
    n["size"] = SIZE[n["group"]]
    n["title"] = n["label"]

GRAPH_JSON = json.dumps(G, ensure_ascii=False).replace("</", "<\\/")

studio_opts = sorted(
    [{"id": n["id"], "name": n["raw"]["name_zh"]} for n in G["nodes"] if n["group"] == "studio"],
    key=lambda x: x["name"])
studio_opt_html = "".join(f'<option value="{o["id"]}">{o["name"]}</option>' for o in studio_opts)

tier1_genres = sorted(
    [n["raw"]["name"] for n in G["nodes"] if n.get("tier1")],
    key=lambda x: x)
genre_opt_html = "".join(f'<option value="{esc_name}">{esc_name}</option>' for esc_name in tier1_genres)

# ---------------------------------------------------------------------------
# 数据刷新（不再持有页面模板）
#
# site/index.html 是手工维护的唯一事实来源 —— GitHub Pages 直接部署它，
# v1.16.x 起的全部页面迭代（移动端适配、筛选抽屉、仓库入口）都直接改它。
# 本文件过去内嵌了一整份 509 行的 HTML 模板，与 index.html 构成两份事实来源：
# 页面每迭代一次二者就漂移一分，跑一次 make site 会用旧模板覆盖新页面、
# 且旧页面照样能打开，移动端成果无声丢失。
#
# 现在本脚本只做一件事：把 data/graph.json 的最新数据写回 index.html 里
# 各「生成区」，标记之外的内容逐字节保留。生成区同时承担两个职责：
#   1. 运行时数据（图谱、下拉选项）
#   2. SEO/GEO 内容 —— 搜索引擎与非 JS 的 AI 爬虫读不到客户端渲染的内容，
#      预渲染的名单表与 JSON-LD 是站点实体词（游戏/工作室/年份）唯一
#      能被爬虫看见的载体，必须与数据同源刷新，否则数据一更新就漂移。
#
# 标记约定（手工维护 index.html 时不可删除）：
#   /*__GRAPH_DATA_START__*/   ... /*__GRAPH_DATA_END__*/     JS 图数据块
#   <!--__STUDIO_OPTS_START__--> ... <!--__STUDIO_OPTS_END__--> 工作室下拉选项
#   <!--__GENRE_OPTS_START__-->  ... <!--__GENRE_OPTS_END__-->  类型下拉选项
#   <!--__GAME_TABLE_START__-->  ... <!--__GAME_TABLE_END__-->  预渲染名单表
#   <!--__SEO_JSONLD_START__-->  ... <!--__SEO_JSONLD_END__-->  JSON-LD 结构化数据
# ---------------------------------------------------------------------------

SITE_URL = "https://weirdgiser.site/goty-knowledge-graph/"


def _esc(x):
    return html.escape(str(x)) if x not in (None, "") else ""


# --- 预渲染名单表：与前端 buildTable() 同样的七列，按年份排序 ---
games = sorted(
    [n for n in G["nodes"] if n["group"] in ("game", "goty")],
    key=lambda n: (n["raw"]["year"], n["raw"]["title_zh"]))
goty_games = [n for n in games if n["raw"].get("is_goty")]
game_rows = []
for n in games:
    r = n["raw"]
    genres = "、".join(r["genres"]) if r.get("genres") else r.get("genre", "")
    rating = r["player_rating"] if r.get("player_rating") not in (None, "") else "—"
    star = '<span class="star">★</span>' if r.get("is_goty") else ""
    game_rows.append(
        f"<tr><td>{r['year']}</td><td>{_esc(r['title_zh'])}</td>"
        f"<td>{_esc(r['title'])}</td><td>{_esc(genres)}</td>"
        f"<td>{_esc(r['developer'])}</td><td>{_esc(rating)}</td><td>{star}</td></tr>")
game_table_html = (
    "<h2>历届年度最佳游戏完整名单（2006–2025）</h2>"
    f"<p>共收录 {len(games)} 款游戏与 {len(goty_games)} 届「年度最佳游戏」"
    f"（Game of the Year，2006–2013 年为 Spike VGA / VGX，2014 年起为 The Game Awards），"
    f"涉及 {sum(1 for n in G['nodes'] if n['group'] == 'studio')} 家开发商、"
    f"{sum(1 for n in G['nodes'] if n['group'] == 'genre')} 类游戏类型。"
    "评分以 Metacritic 媒体均分为参考；GOTY 列的 ★ 标出当届得主。</p>"
    "<table><thead><tr><th>年份</th><th>游戏（中）</th><th>游戏（英）</th>"
    "<th>类型</th><th>开发商</th><th>评分</th><th>GOTY</th></tr></thead><tbody>"
    + "".join(game_rows) + "</tbody></table>")

# --- JSON-LD：Dataset（站点是什么）+ ItemList（20 届得主，AI 抽取的主要入口）。
# 整个 <script> 块是一个生成区：JSON 里不能放 HTML 注释标记，标记必须包在
# script 标签外面。JSON 内的 </ 序列同样要转义，防止提前终止 script。 ---
jsonld = {
    "@context": "https://schema.org",
    "@graph": [
        {
            "@type": "Dataset",
            "name": "历届年度最佳游戏知识图谱（2006–2025）",
            "description": (
                "2006–2025 年「年度最佳游戏」（Game of the Year）结构化数据集："
                f"{len(goty_games)} 届 TGA / Spike VGA 得主、{len(games)} 款游戏、"
                f"{sum(1 for n in G['nodes'] if n['group'] == 'studio')} 家开发商、"
                f"{sum(1 for n in G['nodes'] if n['group'] == 'genre')} 类游戏类型、"
                f"{len(G['edges'])} 条关系。整理自 The Game Awards 与 Metacritic 公开资料。"),
            "url": SITE_URL,
            "keywords": ["年度最佳游戏", "Game of the Year", "TGA", "The Game Awards",
                         "Spike VGA", "游戏奖项", "历届名单", "知识图谱"],
            "creator": {"@type": "Organization", "name": "Coolgiserz",
                        "url": "https://github.com/Coolgiserz"},
            "license": "https://github.com/Coolgiserz/goty-knowledge-graph",
            "temporalCoverage": "2006/2025",
            "variableMeasured": ["年度最佳游戏得主", "开发商", "游戏类型", "Metacritic 媒体均分"],
        },
        {
            "@type": "ItemList",
            "name": "历届年度最佳游戏名单（2006–2025）",
            "numberOfItems": len(goty_games),
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i + 1,
                    "item": {
                        "@type": "VideoGame",
                        "name": n["raw"]["title_zh"],
                        "alternateName": n["raw"]["title"],
                        "datePublished": str(n["raw"]["year"]),
                        "author": {"@type": "Organization", "name": n["raw"]["developer"]},
                    },
                }
                for i, n in enumerate(goty_games)
            ],
        },
    ],
}
jsonld_html = ('<script type="application/ld+json">\n'
               + json.dumps(jsonld, ensure_ascii=False).replace("</", "<\\/")
               + "\n</script>")

# --- llms.txt：面向 AI 爬虫的站点摘要（llmstxt.org 约定）。
# 名单与统计由数据生成，与页面内生成区同源，避免摘要与数据漂移。 ---
llms_lines = [
    "# 年度最佳游戏知识图谱（GOTY Knowledge Graph）",
    "",
    f"> 2006–2025 年「年度最佳游戏」（Game of the Year）结构化数据集："
    f"{len(goty_games)} 届 TGA / Spike VGA 得主、{len(games)} 款游戏、"
    f"{sum(1 for n in G['nodes'] if n['group'] == 'studio')} 家开发商、"
    f"{sum(1 for n in G['nodes'] if n['group'] == 'genre')} 类游戏类型、"
    f"{len(G['edges'])} 条关系。整理自 The Game Awards 与 Metacritic 公开资料，"
    "评分以 Metacritic 媒体均分为参考。",
    "",
    "## 历届年度最佳游戏（Game of the Year）",
]
for n in goty_games:
    r = n["raw"]
    body = ""
    award_nodes = [m for m in G["nodes"] if m["group"] == "award" and m["raw"].get("game_id") == n["id"]]
    if award_nodes:
        body = f"（{award_nodes[0]['raw']['body']}）"
    llms_lines.append(f"- {r['year']}《{_esc(r['title_zh'])}》（{_esc(r['title'])}）— {_esc(r['developer'])}{body}")
llms_lines += [
    "",
    "## 页面",
    f"- [首页（交互知识图谱 + 完整名单表）]({SITE_URL})：按年份 / 工作室 / 类型筛选，点击节点查看游戏详情。",
    "",
    "## 数据说明",
    "- 2006–2013 年奖项为 Spike VGA / VGX，2014 年起为 The Game Awards（TGA）。",
    "- 评分以 Metacritic 媒体均分为参考，仅部分游戏有评分数据。",
    f"- 数据与源码：https://github.com/Coolgiserz/goty-knowledge-graph",
]
llms_text = "\n".join(llms_lines) + "\n"

TEMPLATE_PATH = os.path.join(SITE_DIR, "index.html")
with open(TEMPLATE_PATH, encoding="utf-8") as f:
    html = f.read()

# re.sub 的替换串若是普通字符串，其中的 \ 会被当作反向引用转义；
# GRAPH_JSON 含 <\/ 这类序列，必须用函数替换保证逐字写入。
REGIONS = [
    (r"/\*__GRAPH_DATA_START__\*/.*?/\*__GRAPH_DATA_END__\*/",
     "/*__GRAPH_DATA_START__*/\nconst GRAPH = " + GRAPH_JSON + ";\n/*__GRAPH_DATA_END__*/"),
    (r"<!--__STUDIO_OPTS_START__-->.*?<!--__STUDIO_OPTS_END__-->",
     "<!--__STUDIO_OPTS_START__-->" + studio_opt_html + "<!--__STUDIO_OPTS_END__-->"),
    (r"<!--__GENRE_OPTS_START__-->.*?<!--__GENRE_OPTS_END__-->",
     "<!--__GENRE_OPTS_START__-->" + genre_opt_html + "<!--__GENRE_OPTS_END__-->"),
    (r"<!--__GAME_TABLE_START__-->.*?<!--__GAME_TABLE_END__-->",
     "<!--__GAME_TABLE_START__-->\n" + game_table_html + "\n<!--__GAME_TABLE_END__-->"),
    (r"<!--__SEO_JSONLD_START__-->.*?<!--__SEO_JSONLD_END__-->",
     "<!--__SEO_JSONLD_START__-->\n" + jsonld_html + "\n<!--__SEO_JSONLD_END__-->"),
]

missing = []
for pattern, replacement in REGIONS:
    html, count = re.subn(pattern, lambda _m, r=replacement: r, html, flags=re.DOTALL)
    if count != 1:
        missing.append(pattern)
if missing:
    raise SystemExit(
        "ERROR: site/index.html 的数据标记区缺失或不唯一，未写入任何内容：\n  "
        + "\n  ".join(missing)
        + "\n标记是生成区边界，手工维护时不可删除；请从 git 历史恢复或按上述约定补回。")

with open(TEMPLATE_PATH, "w", encoding="utf-8") as f:
    f.write(html)
with open(os.path.join(SITE_DIR, "llms.txt"), "w", encoding="utf-8") as f:
    f.write(llms_text)
shutil.copyfile(VENDOR_JS, os.path.join(ASSET_DIR, "vis-network.min.js"))
print("site/index.html data refreshed, bytes:", len(html.encode("utf-8")))
print("site/llms.txt written, bytes:", len(llms_text.encode("utf-8")))
print("assets/vis-network.min.js copied:",
      os.path.getsize(os.path.join(ASSET_DIR, "vis-network.min.js")), "bytes")
