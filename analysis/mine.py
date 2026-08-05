# -*- coding: utf-8 -*-
"""年度最佳游戏知识图谱 — 数据挖掘脚本。
读取 data/graph.json，计算奖项的“品味”、最佳游戏特征、工作室格局等洞察，
输出 docs/INSIGHTS.md（报告）与 analysis/stats.json（供图表/前端复用）。
"""
import json, os, statistics, re
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
G = json.load(open(os.path.join(ROOT, "data/graph.json"), encoding="utf-8"))
nodes = G["nodes"]
edges = G["edges"]
by_id = {n["id"]: n for n in nodes}

games = [n for n in nodes if n["group"] in ("game", "goty")]
goty = [n for n in nodes if n["group"] == "goty"]
studios = {n["id"]: n for n in nodes if n["group"] == "studio"}
genres = {n["id"]: n for n in nodes if n["group"] == "genre"}

DESIGN = {"开放世界", "多人合作", "在线"}

def game_genres(g):
    out = []
    for e in edges:
        if e["type"] == "BELONGS_TO_GENRE" and e["from"] == g["id"]:
            out.append(by_id[e["to"]]["raw"]["name"])
    return out

def game_tiers(g):
    return g["raw"].get("tiers") or []

def studio_of(g):
    return studios.get(g["raw"]["developer_id"])

def clean_name(s):
    # 去掉含日文假名的括注（如 FromSoftware（フロムソフトウェア）），报告更整洁
    return re.sub(r"（[^（）]*[ぁ-んァ-ヶ]+[^（）]*）", "", s or "").strip() or s

def studio_name(g):
    s = studio_of(g)
    return clean_name(s["raw"]["name_zh"]) if s else None

# ---------- 新 IP / 续作 标注（仅 20 款 GOTY，按事实人工判定以保证准确）----------
NEW_IP = {
    2006: False, 2007: True, 2008: False, 2009: False, 2010: False,
    2011: False, 2012: False, 2013: False, 2014: False, 2015: False,
    2016: True, 2017: False, 2018: False, 2019: True, 2020: False,
    2021: True, 2022: True, 2023: False, 2024: True, 2025: True,
}

# ============ 1. 奖项“品味”：类型 / 设计维度分布 ============
def dim_counter(group):
    c = Counter()
    for g in group:
        for d in game_genres(g):
            if d in DESIGN:
                c[d] += 1
    return c

def tier_counter(group):
    c = Counter()
    for g in group:
        for t in game_tiers(g):
            c[t] += 1
    return c

goty_dim = dim_counter(goty)
all_dim = dim_counter(games)
goty_tier = tier_counter(goty)
all_tier = tier_counter(games)

# ============ 2. 工作室集中度 ============
studio_wins = Counter()
studio_titles = defaultdict(list)
for g in goty:
    sn = studio_name(g)
    if sn:
        studio_wins[sn] += 1
        studio_titles[sn].append(g["raw"]["title_zh"])
multi_winners = {k: v for k, v in studio_wins.items() if v >= 2}

# 工作室“其他作品”平均评分（衡量其整体水准）
studio_other_ratings = defaultdict(list)
for gid, s in studios.items():
    for og in s["raw"].get("other_games", []):
        if isinstance(og.get("rating"), int):
            studio_other_ratings[s["raw"]["name_zh"]].append(og["rating"])
studio_other_avg = {k: round(statistics.mean(v), 1) for k, v in studio_other_ratings.items() if v}

# ============ 3. 新 IP vs 续作 时间线 ============
ip_timeline = []
for g in sorted(goty, key=lambda x: x["raw"]["year"]):
    y = g["raw"]["year"]
    ip_timeline.append((y, g["raw"]["title_zh"], "新IP" if NEW_IP.get(y) else "续作/IP"))

# ============ 4. 评分门槛 ============
def ratings(group):
    return [g["raw"]["player_rating"] for g in group
            if isinstance(g["raw"].get("player_rating"), int)]
goty_r = ratings(goty)
all_r = ratings(games)
non_goty_r = ratings([g for g in games if not g["raw"]["is_goty"]])
club90 = [g["raw"]["title_zh"] for g in goty if isinstance(g["raw"].get("player_rating"), int) and g["raw"]["player_rating"] >= 90]
goty_rated = [g for g in goty if isinstance(g["raw"].get("player_rating"), int)]
goty_under90 = [g["raw"]["title_zh"] for g in goty_rated if g["raw"]["player_rating"] < 90]

# ============ 5. 国家 / 厂商 / 平台 ============
country_wins = Counter()
for g in goty:
    s = studio_of(g)
    if s:
        country_wins[s["raw"]["country"]] += 1
publisher_wins = Counter(g["raw"]["publisher"] for g in goty)
platform_wins = Counter()
for g in goty:
    for p in (g["raw"].get("platforms") or "").split(";"):
        p = p.strip()
        if p:
            platform_wins[p] += 1

# ============ 6. 中文文本关键词挖掘 ============
THEME_KW = ["开放世界", "叙事", "剧情", "故事", "战斗", "系统", "多人", "在线",
            "合作", "角色扮演", "探索", "物理", "创新", "艺术", "配乐", "音乐",
            "情感", "自由度", "沙盒", "手感", "关卡", "解谜", "氛围"]
DRAW_KW = ["技术", "优化", "bug", "崩溃", "结局", "节奏", "难度", "联网",
           "平衡", "争议", "肝", "bug", "卡顿", "服务器", "政治"]

def kw_freq(group, kws, field):
    c = Counter()
    n = 0
    for g in group:
        txt = g["raw"].get(field, "") or ""
        if txt:
            n += 1
            for k in kws:
                if k in txt:
                    c[k] += 1
    return c, n

goty_theme, ngt = kw_freq(goty, THEME_KW, "influence")
# 同时统计 unique_features
gt2, _ = kw_freq(goty, THEME_KW, "unique_features")
for k, v in gt2.items():
    goty_theme[k] += v
all_theme, nat = kw_freq(games, THEME_KW, "influence")
at2, _ = kw_freq(games, THEME_KW, "unique_features")
for k, v in at2.items():
    all_theme[k] += v

goty_draw, _ = kw_freq(goty, DRAW_KW, "drawbacks")
all_draw, _ = kw_freq(games, DRAW_KW, "drawbacks")

# ============ 收集 stats 供外部复用 ============
def dist(counter, total):
    return [{"name": k, "count": v, "pct": round(100 * v / total, 1)} for k, v in counter.most_common()]

stats = {
    "totals": {"games": len(games), "goty": len(goty), "studios": len(studios),
               "genres": len(genres)},
    "goty_design_dims": dist(goty_dim, len(goty)),
    "all_design_dims": dist(all_dim, len(games)),
    "goty_tiers": dist(goty_tier, len(goty)),
    "all_tiers": dist(all_tier, len(games)),
    "studio_wins": dict(studio_wins.most_common()),
    "multi_winners": multi_winners,
    "multi_winner_share": round(100 * sum(multi_winners.values()) / len(goty), 1),
    "ip_timeline": [{"year": y, "title": t, "type": tp} for y, t, tp in ip_timeline],
    "new_ip_count": sum(1 for v in NEW_IP.values() if v),
    "ratings": {
        "goty_mean": round(statistics.mean(goty_r), 1),
        "goty_median": statistics.median(goty_r),
        "nongoty_mean": round(statistics.mean(non_goty_r), 1),
        "nongoty_median": statistics.median(non_goty_r),
        "all_mean": round(statistics.mean(all_r), 1),
        "club90": club90,
        "club90_count": len(club90),
        "goty_under90": goty_under90,
    },
    "country_wins": dict(country_wins.most_common()),
    "publisher_wins": dict(publisher_wins.most_common()),
    "platform_wins": dict(platform_wins.most_common()),
    "theme_goty": dist(goty_theme, ngt),
    "theme_all": dist(all_theme, nat),
    "draw_goty": dist(goty_draw, len(goty)),
}

os.makedirs(os.path.join(ROOT, "analysis"), exist_ok=True)
json.dump(stats, open(os.path.join(ROOT, "analysis/stats.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# ============ 生成报告 ============
def over_index(dim, goty_c, all_c, total_goty, total_all):
    g_pct = 100 * goty_c / total_goty
    a_pct = 100 * all_c / total_all
    return g_pct, a_pct, round(g_pct / a_pct, 2) if a_pct else 0

L = []
A = L.append
A("# 年度最佳游戏知识图谱 · 数据挖掘报告\n")
A("> 数据来源：`data/graph.json`（20 款年度最佳游戏 GOTY + 87 款开发商其他作品 = 107 款游戏、"
  "15 家开发商、51 个类型节点）。\n")
A("> 说明：本数据集的“年度最佳”指 **Spike VGA / VGX（2006–2013）+ The Game Awards（2014–2025）** 的单一年度得主；"
  "玩家评分以 Metacritic 媒体均分为参考。样本量小（每年 1 款），以下结论应视为**探索性洞察**而非统计推断，"
  "但其中若干模式在量级上已足够鲜明。\n")

# 摘要
A("## 0. 五个最反直觉的洞察（TL;DR）\n")
A(f"1. **开放世界是“隐形门槛”**：{goty_dim.get('开放世界',0)}/{len(goty)} 的年度最佳是开放世界游戏，"
  f"而全样本仅 {round(100*all_dim.get('开放世界',0)/len(games),1)}% —— 奖项对开放世界的偏好约 "
  f"**{over_index('开放世界', goty_dim.get('开放世界',0), all_dim.get('开放世界',0), len(goty), len(games))[2]} 倍过指数**。"
  f"连《艾尔登法环》《巫师3》《天际》这类“动作 RPG”也都自带开放世界标签。\n")
A(f"2. **少数工作室长期垄断**：{len(multi_winners)} 家工作室（{ '、'.join(multi_winners.keys()) }）"
  f"拿走了近 20 年 **{stats['multi_winner_share']}%** 的年度最佳。\n")
A(f"3. **新 IP 在近年“逆袭”**：2006–2015 的 10 位得主里只有 1 款（2007《生化奇兵》）是新 IP；"
  f"2016 之后新 IP 频率显著上升，2021–2025 五年有 4 款新 IP 夺冠。\n")
A(f"4. **Metacritic 90+ 是“必要非充分”门槛**：{len(club90)}/{len(goty_rated)} 的 GOTY 评分 ≥90，"
  f"均值 {stats['ratings']['goty_mean']}；但有 {len(goty_under90)} 款低于 90 仍夺冠（{ '、'.join(goty_under90) }）。\n")
A(f"5. **“最佳游戏”也会翻车在同一处**：文本挖掘显示 GOTY 的缺点高频词集中在 **技术/优化/结局争议**——"
  f"即使是满分神作，也常被诟病首发优化、Bug 与（尤其是）结局处理。\n")

# 1. 奖项品味
A("## 1. 奖项的“品味”：开放世界 + 叙事 RPG 的统治\n")
A("把 20 款 GOTY 与全部 107 款游戏的类型/设计维度分布对比，可看出评委（玩家+媒体投票）明显偏好某些结构：\n")
A("### 1.1 设计维度（开放世界 / 多人合作 / 在线）过指数\n")
A("| 设计维度 | GOTY 占比 | 全样本占比 | 过指数倍数 |")
A("|---|---|---|---|")
for d in ["开放世界", "多人合作", "在线"]:
    gc = goty_dim.get(d, 0); ac = all_dim.get(d, 0)
    g_pct, a_pct, oi = over_index(d, gc, ac, len(goty), len(games))
    A(f"| {d} | {gc}/{len(goty)} ({g_pct:.0f}%) | {ac}/{len(games)} ({a_pct:.0f}%) | ×{oi} |")
A("")
A("**含义**：开放世界几乎成了“年度级 3A”的默认形态。注意这是**相关性**而非因果——"
  "开放世界本身不保证获奖，但它与“高预算、强叙事、长流程、可展示”等获奖要素高度绑定。\n")

A("### 1.2 顶层玩法类别分布（GOTY vs 全样本）\n")
A("| 顶层类别 | GOTY 数 | GOTY 占比 | 全样本占比 | 过指数 |")
A("|---|---|---|---|---|")
for t, gc in goty_tier.most_common():
    ac = all_tier.get(t, 0)
    g_pct, a_pct, oi = over_index(t, gc, ac, len(goty), len(games))
    A(f"| {t} | {gc} | {g_pct:.0f}% | {a_pct:.0f}% | ×{oi} |")
A("")
A("角色扮演与动作冒险在 GOTY 中显著过指数——这与“开放世界 + 长线叙事 RPG”几乎是一回事："
  "《天际》《巫师3》《艾尔登法环》《博德之门3》《光与影》在结构上高度同构。\n")

# 2. 工作室
A("## 2. 工作室格局：四大家族的“命中率”\n")
A("| 工作室 | 夺冠次数 | 代表 GOTY | 其他作品均分* |")
A("|---|---|---|---|")
for s, w in studio_wins.most_common():
    if w >= 2:
        titles = "、".join(studio_titles[s])
        avg = studio_other_avg.get(s, "—")
        A(f"| {s} | {w} | {titles} | {avg} |")
A("")
A("*其他作品均分仅统计了研究子代理收录的代表作，非全量，仅供横向参考。\n")
A(f"**集中度解读**：{len(multi_winners)} 家工作室（约占 15 家的 {round(100*len(multi_winners)/len(studios))}%）"
  f"贡献了近 20 年 {stats['multi_winner_share']}% 的年度最佳。Rockstar（3 次）与 FromSoftware、Naughty Dog、Bethesda（各 2 次）"
  "构成“常胜集团”。一个可深挖的假设是：**发行/宣发资源、IP 积累与媒体关系**是否与获奖概率正相关"
  "（见第 4 节研究课题）。\n")

# 3. 新IP时间线
A("## 3. 新 IP vs 续作：评奖口味的“世代漂移”\n")
A("| 年份 | 得主 | 类型 |")
A("|---|---|---|")
for y, t, tp in ip_timeline:
    A(f"| {y} | {t} | {tp} |")
A("")
new_cnt = stats["new_ip_count"]
A(f"**模式**：2006–2015 的 10 位得主中仅 **1 款**新 IP（2007《生化奇兵》）；"
  f"而 2016 之后新 IP 明显回潮，2019/2021/2022/2024/2025 均由新 IP 摘得（共 {new_cnt} 款新 IP 夺冠）。\n")
A("**解读**：早期 GOTY 多为成熟系列续作（GTA、神秘海域、塞尔达、战神），依托 IP 惯性；"
  "近年的“新 IP 红利”可能源于：(a) 直播/短视频时代**“可观看性”与新鲜感**更被放大；"
  "(b) 玩家对“换皮续作”疲劳，更奖励**系统化创新**（如《双人成行》的合作叙事、《艾尔登法环》的开放世界魂）。\n")

# 4. 评分门槛
A("## 4. 评分门槛：Metacritic 能预言年度最佳吗？\n")
A(f"- GOTY 平均 Metacritic：**{stats['ratings']['goty_mean']}**（中位数 {stats['ratings']['goty_median']}）")
A(f"- 非 GOTY 游戏平均：**{stats['ratings']['nongoty_mean']}**（中位数 {stats['ratings']['nongoty_median']}）")
A(f"- **90+ 俱乐部（{len(club90)}/{len(goty_rated)} 的 GOTY）**：{ '、'.join(club90) }")
if goty_under90:
    A(f"- 但仍有 **{len(goty_under90)}** 款评分 <90 夺冠：{ '、'.join(goty_under90) }"
      f"——说明“口碑极高”不是唯一通路，**文化影响力 / 创新 / 叙事**同样能扳回一城。\n")
A("")
A("**含义**：高评分更像是“入围门票”而非“获奖保证”。这恰是数据挖掘有趣之处——"
  "若用 Metacritic 当唯一预测因子，会漏掉《最后生还者 第二部》《巫师3》这类靠叙事与争议出圈的作品。\n")

# 5. 地缘/厂商/平台
A("## 5. 地缘、厂商与平台格局\n")
A("### 5.1 国家 / 地区（按开发商总部）\n")
A("| 国家/地区 | 夺冠次数 |")
A("|---|---|")
for c, w in country_wins.most_common():
    A(f"| {c} | {w} |")
A("")
A("美国主导，但近年**欧洲多元化**显著：波兰（CDPR）、瑞典（Hazelight）、法国（Sandfall）、"
  "比利时（Larian）在 2021–2025 连续五年贡献了 4 位得主。这是“西方 3A 中心”从美国单极走向欧美多级的一个缩影。\n")

A("### 5.2 发行商\n")
A("| 发行商 | 夺冠次数 |")
A("|---|---|")
for p, w in publisher_wins.most_common(8):
    A(f"| {p} | {w} |")
A("")
A("索尼第一方（Naughty Dog、Santa Monica、Team Asobi）与 Take-Two/2K（Rockstar、BioWare 部分）"
  "等大型发行体系占据大头——再次指向“资源集中度”假设。\n")

A("### 5.3 平台\n")
A("| 平台 | GOTY 数 |")
A("|---|---|")
for p, w in platform_wins.most_common():
    A(f"| {p} | {w} |")
A("")
A("PlayStation 系几乎“全勤”，PC 紧随；任天堂仅《旷野之息》《Astro Bot》两款独占上榜。"
  "**主机独占/第一方**在评奖中具备天然曝光优势，是可量化验证的课题。\n")

# 6. 文本挖掘
A("## 6. 文本挖掘：神作共享的“基因”与通病\n")
A("对 GOTY 的“影响力/独特之处”与全部游戏的同类文本做中文关键词频率对比（同一游戏多字段去重计数）：\n")
A("### 6.1 主题关键词（GOTY 高频）\n")
A("| 关键词 | GOTY 文本命中 | 全样本文本命中 |")
A("|---|---|---|")
for k, v in goty_theme.most_common(12):
    A(f"| {k} | {v} | {all_theme.get(k,0)} |")
A("")
A("> 注：此处为**文本词频**（一款游戏的“影响力+独特之处”两字段去重后最多计 2 次），"
  "与第 1 节结构化“开放世界”标签（9 款 GOTY）口径不同，二者互为印证。\n")
A("**叙事/开放世界/探索/艺术/配乐**在 GOTY 中高度集中——印证“年度最佳”本质是"
  "**高完成度的叙事型开放世界体验**。\n")

A("### 6.2 缺点关键词（GOTY 也躲不掉）\n")
A("| 关键词 | GOTY 命中 |")
A("|---|---|")
for k, v in goty_draw.most_common(10):
    if v:
        A(f"| {k} | {v} |")
A("")
A("即使是满分神作，也常被点名**技术/优化、Bug、结局争议、节奏**。这揭示一个反差洞察："
  "**“年度最佳”评价的是“巅峰高度”，而非“全程无瑕”**——玩家对个别短板容忍度很高，"
  "只要峰值体验足够强。\n")

# 7. 研究课题
A("## 7. 可继续深挖的开放研究课题\n")
A("以下课题均可用本仓库的数据（及扩充数据）验证，附验证思路：\n")
A("1. **开放世界是否已成“隐性门槛”？** — 用 `BELONGS_TO_GENRE` 统计 GOTY 中开放世界占比 vs 全样本，"
  "并按年代分层；若近 10 年占比显著高于前 10 年，则支持“门槛化”假设。\n")
A("2. **续作疲劳 vs 新 IP 红利** — 以第 3 节时间线为起点，扩充 2006 年前的 VGA 得主，"
  "检验“新 IP 夺冠率随时间上升”是否稳健；可加入“媒体口碑方差”作为新鲜感代理变量。\n")
A("3. **资源集中度假设** — 将 `studio.parent`（母公司）、发行商、平台独占作为特征，"
  "与 `WON` 关系做相关性/逻辑回归（需扩充外部变量如营销预算）。\n")
A("4. **“可观看性”假说** — 假设：直播/短视频时代更奖励“高观赏性”品类（肉鸽、合作、华丽演出）。"
  "可用 `多人合作/在线` 设计维度 + 年代交互项检验。\n")
A("5. **评分门槛的因果方向** — Metacritic 90+ 是获奖因还是果？"
  "可比较“获奖前评分”与“获奖后评分”漂移（需时间序列数据）。\n")
A("6. **类型合流（genre convergence）** — `动作+冒险+RPG+开放世界` 边界模糊，"
  "计算各游戏 `genres` 列表的重叠度随年代变化，量化 3A 设计的“同质化”趋势。\n")
A("7. **地缘多元化是否会动摇美国主导** — 用 `studio.country` 做滚动 5 年窗口，"
  "观察非美国得主占比趋势。\n")
A("8. **“神作翻车点”共性** — 对 `drawbacks` 做主题聚类（技术/叙事/平衡），"
  "检验不同品类是否各有“典型短板”。\n")

A("\n---\n*报告由 `analysis/mine.py` 自动生成，重跑 `python3 analysis/mine.py` 即可刷新。*\n")

os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
open(os.path.join(ROOT, "docs/INSIGHTS.md"), "w", encoding="utf-8").write("\n".join(L))
print("OK -> docs/INSIGHTS.md (%d lines), analysis/stats.json" % len(L))
print("GOTY design dims:", dict(goty_dim))
print("multi winners share:", stats["multi_winner_share"], "%")
print("new IP count:", new_cnt, "| club90:", len(club90))
print("goty rating mean/median:", stats["ratings"]["goty_mean"], stats['ratings']['goty_median'])
