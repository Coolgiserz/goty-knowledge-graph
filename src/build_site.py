#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build site/index.html (knowledge-graph explorer) from data/graph.json.

Run from anywhere:
  python3 src/build_site.py
"""
import json, os, re, shutil

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
# 三处由标记包围的「生成区」，标记之外的内容逐字节保留。
#
# 标记约定（手工维护 index.html 时不可删除）：
#   /*__GRAPH_DATA_START__*/   ... /*__GRAPH_DATA_END__*/     JS 图数据块
#   <!--__STUDIO_OPTS_START__--> ... <!--__STUDIO_OPTS_END__--> 工作室下拉选项
#   <!--__GENRE_OPTS_START__-->  ... <!--__GENRE_OPTS_END__-->  类型下拉选项
# ---------------------------------------------------------------------------

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
shutil.copyfile(VENDOR_JS, os.path.join(ASSET_DIR, "vis-network.min.js"))
print("site/index.html data refreshed, bytes:", len(html.encode("utf-8")))
print("assets/vis-network.min.js copied:",
      os.path.getsize(os.path.join(ASSET_DIR, "vis-network.min.js")), "bytes")
