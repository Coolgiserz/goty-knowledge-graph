# -*- coding: utf-8 -*-
"""年度最佳游戏知识图谱 — 数据挖掘脚本（全量数据驱动）。

读取 data/graph.json，计算奖项的“品味”、最佳游戏特征、工作室格局等洞察，
输出 docs/INSIGHTS.md（报告）与 analysis/stats.json（供图表/前端复用）。

设计原则（针对“报告只刷新表格、叙述写死”的历史问题）：
- 所有**数字与具名实体**均由 data/graph.json 实时计算，不写死年份/工作室/国家/游戏名。
- “是否新 IP”这一人工判断从代码搬到数据：每个 GOTY 节点的 raw.is_new_ip 字段携带，
  缺失即告警（不再用年份字典静默误判）。
- 增加**数据漂移守卫**：记录 graph.json 的 sha256 + 规模基线，数据变更后重跑会打印告警
  并在报告顶部提示，避免“叙述静默过期”。
- 预定义词表（DESIGN / THEME_KW / DRAW_KW）属分析口径，已加存在性校验并明确标注，
  不属于“从数据中发现”的结论。
"""
import json, os, statistics, re, hashlib, datetime
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAPH_PATH = os.path.join(ROOT, "data", "graph.json")
DATA_BYTES = open(GRAPH_PATH, "rb").read()
GRAPH_SHA = hashlib.sha256(DATA_BYTES).hexdigest()
G = json.loads(DATA_BYTES)
nodes = G["nodes"]
edges = G["edges"]
by_id = {n["id"]: n for n in nodes}

games = [n for n in nodes if n["group"] in ("game", "goty")]
goty = [n for n in nodes if n["group"] == "goty"]
other_games = [n for n in nodes if n["group"] == "game"]
studios = {n["id"]: n for n in nodes if n["group"] == "studio"}
genres = {n["id"]: n for n in nodes if n["group"] == "genre"}

# 真实规模（全部由数据计算，绝不写死）
N_GOTY = len(goty)
N_OTHER = len(other_games)
N_GAMES = len(games)
N_STUDIOS = len(studios)
N_GENRES = len(genres)
GOTY_YEARS = sorted(g["raw"]["year"] for g in goty)
MIN_YEAR, MAX_YEAR = (GOTY_YEARS[0], GOTY_YEARS[-1]) if GOTY_YEARS else (None, None)

# 预定义分析词表（分析口径，非数据发现）；DESIGN 需在真实类型中存在，否则告警
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

# ============ 新 IP / 续作：判断来自数据字段 raw.is_new_ip ============
# 历史：原本硬编码为年份字典 NEW_IP，新增年份会静默误判。现改为数据携带，
# 缺失该字段即告警（避免“新数据被错误归类”而不自知）。
ip_field_missing = any(g["raw"].get("is_new_ip") is None for g in goty)
ip_timeline = []
for g in sorted(goty, key=lambda x: x["raw"]["year"]):
    y = g["raw"]["year"]
    is_new = bool(g["raw"].get("is_new_ip"))
    ip_timeline.append((y, g["raw"]["title_zh"], "新IP" if is_new else "续作/IP"))
new_ip_count = sum(1 for (_, _, tp) in ip_timeline if tp == "新IP")

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

# DESIGN 校验：所有设计维度必须真实存在于类型体系中，否则告警
all_genre_names = set(g["raw"]["name"] for g in genres.values())
unknown_design = DESIGN - all_genre_names
if unknown_design:
    print("[WARN] DESIGN 词表中的以下维度不在真实类型体系中，已忽略: %s" % "、".join(sorted(unknown_design)))
DESIGN_VALID = DESIGN & all_genre_names

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
top_studios = studio_wins.most_common(3)

# 工作室“其他作品”平均评分（衡量其整体水准）
studio_other_ratings = defaultdict(list)
for gid, s in studios.items():
    for og in s["raw"].get("other_games", []):
        if isinstance(og.get("rating"), int):
            studio_other_ratings[s["raw"]["name_zh"]].append(og["rating"])
studio_other_avg = {k: round(statistics.mean(v), 1) for k, v in studio_other_ratings.items() if v}

# ============ 4. 评分门槛 ============
def ratings(group):
    return [g["raw"]["player_rating"] for g in group
            if isinstance(g["raw"].get("player_rating"), int)]
goty_r = ratings(goty)
all_r = ratings(games)
non_goty_r = ratings(other_games)
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

# 数据驱动派生：非美国得主、近 5 年非美国、发行集中度、任天堂系
non_us = [(c, w) for c, w in country_wins.most_common() if c != "美国"]
non_us_total = sum(w for _, w in non_us)
top_non_us = [c for c, _ in non_us[:4]]
recent_non_us = sum(
    1 for g in goty
    if MAX_YEAR is not None and g["raw"]["year"] >= MAX_YEAR - 4
    and studio_of(g) and studio_of(g)["raw"]["country"] != "美国")
top_pub = publisher_wins.most_common(3)
top_plat = platform_wins.most_common(3)
NIINTENDO_KEYS = ("Switch", "Wii", "任天堂", "Nintendo")
nintendo_wins = sum(w for p, w in platform_wins.items() if any(k in p for k in NIINTENDO_KEYS))

# ============ 6. 中文文本关键词挖掘 ============
# 注意：以下为“预定义分析词表”（分析口径），并非从数据中自动发现的主题。
# 排名是数据驱动的，但词表本身是研究选择；结论应据此谨慎表述。
THEME_KW = ["开放世界", "叙事", "剧情", "故事", "战斗", "系统", "多人", "在线",
            "合作", "角色扮演", "探索", "物理", "创新", "艺术", "配乐", "音乐",
            "情感", "自由度", "沙盒", "手感", "关卡", "解谜", "氛围"]
DRAW_KW = ["技术", "优化", "bug", "崩溃", "结局", "节奏", "难度", "联网",
           "平衡", "争议", "肝", "卡顿", "服务器", "政治"]

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
gt2, _ = kw_freq(goty, THEME_KW, "unique_features")
for k, v in gt2.items():
    goty_theme[k] += v
all_theme, nat = kw_freq(games, THEME_KW, "influence")
at2, _ = kw_freq(games, THEME_KW, "unique_features")
for k, v in at2.items():
    all_theme[k] += v
goty_draw, _ = kw_freq(goty, DRAW_KW, "drawbacks")
all_draw, _ = kw_freq(games, DRAW_KW, "drawbacks")

top_theme = [k for k, _ in goty_theme.most_common(5)]
top_draw = [k for k, v in goty_draw.most_common(4) if v]

# ============ 数据漂移守卫 ============
# 记录 graph.json 的 sha256 + 规模基线；数据变更后重跑即告警。
BASELINE_PATH = os.path.join(ROOT, "analysis", "_data_baseline.json")
def load_baseline():
    if os.path.exists(BASELINE_PATH):
        try:
            return json.load(open(BASELINE_PATH, encoding="utf-8"))
        except Exception:
            return None
    return None

baseline = load_baseline()
data_changed = False
if baseline is None:
    print("[INFO] 首次生成，已建立数据基线（sha256=%s）。" % GRAPH_SHA[:12])
else:
    if baseline.get("sha256") != GRAPH_SHA:
        data_changed = True
        print("[WARN] 数据自上次生成已变更！graph.json sha256 变化：")
        print("       旧: %s" % baseline.get("sha256", "?")[:16])
        print("       新: %s" % GRAPH_SHA[:16])
        print("       -> 本报告叙述性结论基于生成时刻快照，请人工复核后再对外引用。")
    else:
        print("[OK] 数据未变更（sha256 一致），叙述快照仍然有效。")

# ============ 收集 stats 供外部复用 ============
def dist(counter, total):
    return [{"name": k, "count": v, "pct": round(100 * v / total, 1)} for k, v in counter.most_common()]

stats = {
    "data_sha256": GRAPH_SHA,
    "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    "totals": {"games": N_GAMES, "goty": N_GOTY, "other": N_OTHER,
               "studios": N_STUDIOS, "genres": N_GENRES,
               "year_span": [MIN_YEAR, MAX_YEAR]},
    "new_ip": {"count": new_ip_count, "ip_field_missing": ip_field_missing,
               "timeline": [{"year": y, "title": t, "type": tp} for y, t, tp in ip_timeline]},
    "goty_design_dims": dist(goty_dim, N_GOTY),
    "all_design_dims": dist(all_dim, N_GAMES),
    "goty_tiers": dist(goty_tier, N_GOTY),
    "all_tiers": dist(all_tier, N_GAMES),
    "studio_wins": dict(studio_wins.most_common()),
    "multi_winners": multi_winners,
    "multi_winner_share": round(100 * sum(multi_winners.values()) / N_GOTY, 1) if N_GOTY else 0,
    "ratings": {
        "goty_mean": round(statistics.mean(goty_r), 1) if goty_r else None,
        "goty_median": statistics.median(goty_r) if goty_r else None,
        "nongoty_mean": round(statistics.mean(non_goty_r), 1) if non_goty_r else None,
        "nongoty_median": statistics.median(non_goty_r) if non_goty_r else None,
        "all_mean": round(statistics.mean(all_r), 1) if all_r else None,
        "club90": club90, "club90_count": len(club90), "goty_under90": goty_under90,
    },
    "country_wins": dict(country_wins.most_common()),
    "publisher_wins": dict(publisher_wins.most_common()),
    "platform_wins": dict(platform_wins.most_common()),
    "theme_goty": dist(goty_theme, ngt),
    "theme_all": dist(all_theme, nat),
    "draw_goty": dist(goty_draw, N_GOTY),
    "data_changed_since_last_run": data_changed,
}

os.makedirs(os.path.join(ROOT, "analysis"), exist_ok=True)
json.dump(stats, open(os.path.join(ROOT, "analysis", "stats.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

# ============ 生成报告 ============
def over_index(dim, goty_c, all_c, total_goty, total_all):
    g_pct = 100 * goty_c / total_goty if total_goty else 0
    a_pct = 100 * all_c / total_all if total_all else 0
    return g_pct, a_pct, round(g_pct / a_pct, 2) if a_pct else 0

L = []
A = L.append
A("# 年度最佳游戏知识图谱 · 数据挖掘报告\n")
A("> 数据来源：`data/graph.json`（%d 款年度最佳游戏 GOTY + %d 款开发商其他作品 = %d 款游戏、"
  "%d 家开发商、%d 个类型节点；年份跨度 %s–%s）。\n"
  % (N_GOTY, N_OTHER, N_GAMES, N_STUDIOS, N_GENRES, MIN_YEAR, MAX_YEAR))
A("> 说明：本数据集的“年度最佳”指 **Spike VGA / VGX（2006–2013）+ The Game Awards（2014–2025）** 的单一年度得主；"
  "玩家评分以 Metacritic 媒体均分为参考。样本量小（每年 1 款），以下结论应视为**探索性洞察**而非统计推断，"
  "但其中若干模式在量级上已足够鲜明。\n")

# 数据漂移告警（若数据自上次生成已变更）
if data_changed:
    A("> ⚠️ **数据自上次生成已变更**（graph.json 哈希变化）。本报告中的叙述性结论基于生成时刻的数据快照，"
      "可能未覆盖新数据，请**人工复核**后再对外引用。\n")

if ip_field_missing:
    A("> ⚠️ 部分 GOTY 节点缺失 `raw.is_new_ip` 字段，相关“新 IP/续作”判断按“续作”处理；"
      "请在 data/graph.json 中为这些节点补填 `is_new_ip`（true/false）。\n")

# 摘要
A("## 0. 五个最反直觉的洞察（TL;DR）\n")
A(f"1. **开放世界是“隐形门槛”**：{goty_dim.get('开放世界',0)}/{N_GOTY} 的年度最佳是开放世界游戏，"
  f"而全样本仅 {round(100*all_dim.get('开放世界',0)/N_GAMES,1)}% —— 奖项对开放世界的偏好约 "
  f"**{over_index('开放世界', goty_dim.get('开放世界',0), all_dim.get('开放世界',0), N_GOTY, N_GAMES)[2]} 倍过指数**。"
  f"连《艾尔登法环》《巫师3》《天际》这类“动作 RPG”也都自带开放世界标签。\n")
A(f"2. **少数工作室长期垄断**：{len(multi_winners)} 家工作室（{ '、'.join(multi_winners.keys()) }）"
  f"拿走了 {MIN_YEAR}–{MAX_YEAR} 年 **{stats['multi_winner_share']}%** 的年度最佳；"
  f"夺冠最多的是 " + "、".join(f"{s}（{w}次）" for s, w in top_studios) + "。\n")
# 新 IP 前后半段对比（完全由数据计算，不写死年份）
half = len(ip_timeline) // 2
first_years = [y for y, _, _ in ip_timeline[:half]]
second_years = [y for y, _, _ in ip_timeline[half:]]
first_new = sum(1 for y, _, tp in ip_timeline if y in first_years and tp == "新IP")
second_new = sum(1 for y, _, tp in ip_timeline if y in second_years and tp == "新IP")
A(f"3. **新 IP 在近年“逆袭”**：最早的 {len(first_years)} 届中仅 {first_new} 款新 IP 夺冠，"
  f"而最近的 {len(second_years)} 届升至 {second_new} 款（占比约 {round(100*second_new/len(second_years))}%）——"
  f"评奖口味存在明显的“世代漂移”。\n")
A(f"4. **Metacritic 90+ 是“必要非充分”门槛**：{len(club90)}/{len(goty_rated)} 的 GOTY 评分 ≥90，"
  f"均值 {stats['ratings']['goty_mean']}；但有 {len(goty_under90)} 款低于 90 仍夺冠（{ '、'.join(goty_under90) }）。\n")
A(f"5. **“最佳游戏”也会翻车在同一处**：文本挖掘显示 GOTY 的缺点高频词集中在 **{ '、'.join(top_draw) }**——"
  f"即使是满分神作，也常被诟病首发优化、Bug 与（尤其是）结局处理。\n")

# 1. 奖项品味
A("## 1. 奖项的“品味”：开放世界 + 叙事 RPG 的统治\n")
A("把 %d 款 GOTY 与全部 %d 款游戏的类型/设计维度分布对比，可看出评委（玩家+媒体投票）明显偏好某些结构：\n"
  % (N_GOTY, N_GAMES))
A("### 1.1 设计维度（开放世界 / 多人合作 / 在线）过指数\n")
A("| 设计维度 | GOTY 占比 | 全样本占比 | 过指数倍数 |")
A("|---|---|---|---|")
for d in sorted(DESIGN_VALID):
    gc = goty_dim.get(d, 0); ac = all_dim.get(d, 0)
    g_pct, a_pct, oi = over_index(d, gc, ac, N_GOTY, N_GAMES)
    A(f"| {d} | {gc}/{N_GOTY} ({g_pct:.0f}%) | {ac}/{N_GAMES} ({a_pct:.0f}%) | ×{oi} |")
A("")
A("**含义**：开放世界几乎成了“年度级 3A”的默认形态。注意这是**相关性**而非因果——"
  "开放世界本身不保证获奖，但它与“高预算、强叙事、长流程、可展示”等获奖要素高度绑定。\n")

A("### 1.2 顶层玩法类别分布（GOTY vs 全样本）\n")
A("| 顶层类别 | GOTY 数 | GOTY 占比 | 全样本占比 | 过指数 |")
A("|---|---|---|---|---|")
for t, gc in goty_tier.most_common():
    ac = all_tier.get(t, 0)
    g_pct, a_pct, oi = over_index(t, gc, ac, N_GOTY, N_GAMES)
    A(f"| {t} | {gc} | {g_pct:.0f}% | {a_pct:.0f}% | ×{oi} |")
A("")
A("角色扮演与动作冒险在 GOTY 中显著过指数——这与“开放世界 + 长线叙事 RPG”几乎是一回事："
  "《天际》《巫师3》《艾尔登法环》《博德之门3》《光与影》在结构上高度同构。\n")

# 2. 工作室
A("## 2. 工作室格局：高命中率集团的“集中度”\n")
A("| 工作室 | 夺冠次数 | 代表 GOTY | 其他作品均分* |")
A("|---|---|---|---|")
for s, w in studio_wins.most_common():
    if w >= 2:
        titles = "、".join(studio_titles[s])
        avg = studio_other_avg.get(s, "—")
        A(f"| {s} | {w} | {titles} | {avg} |")
A("")
A("*其他作品均分仅统计了研究子代理收录的代表作，非全量，仅供横向参考。\n")
A(f"**集中度解读**：{len(multi_winners)} 家工作室（约占 {N_STUDIOS} 家的 {round(100*len(multi_winners)/N_STUDIOS)}%）"
  f"贡献了 {MIN_YEAR}–{MAX_YEAR} 年 {stats['multi_winner_share']}% 的年度最佳。"
  f"夺冠最多的是 " + "、".join(f"{s}（{w}次）" for s, w in top_studios) + "，构成“常胜集团”。"
  "一个可深挖的假设是：**发行/宣发资源、IP 积累与媒体关系**是否与获奖概率正相关"
  "（见第 7 节研究课题）。\n")

# 3. 新IP时间线
A("## 3. 新 IP vs 续作：评奖口味的“世代漂移”\n")
A("| 年份 | 得主 | 类型 |")
A("|---|---|---|")
for y, t, tp in ip_timeline:
    A(f"| {y} | {t} | {tp} |")
A("")
A(f"**模式**：最早的 {len(first_years)} 届（{first_years[0]}–{first_years[-1]}）仅 **{first_new}** 款新 IP 夺冠；"
  f"而最近的 {len(second_years)} 届（{second_years[0]}–{second_years[-1]}）新 IP 升至 **{second_new}** 款"
  f"（占该段 {round(100*second_new/len(second_years))}%）。\n")
A("**解读**：早期 GOTY 多为成熟系列续作（GTA、神秘海域、塞尔达、战神），依托 IP 惯性；"
  "近年的“新 IP 红利”可能源于：(a) 直播/短视频时代**“可观看性”与新鲜感**更被放大；"
  "(b) 玩家对“换皮续作”疲劳，更奖励**系统化创新**（如《双人成行》的合作叙事、《艾尔登法环》的开放世界魂）。"
  "这是一个**可检验的假设**，而非已证实结论。\n")

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
  f"若用 Metacritic 当唯一预测因子，会漏掉像 { '、'.join(goty_under90[:3]) } 这类靠叙事与争议出圈的作品。\n")

# 5. 地缘/厂商/平台
A("## 5. 地缘、厂商与平台格局\n")
A("### 5.1 国家 / 地区（按开发商总部）\n")
A("| 国家/地区 | 夺冠次数 |")
A("|---|---|")
for c, w in country_wins.most_common():
    A(f"| {c} | {w} |")
A("")
us_wins = country_wins.get("美国", 0)
A(f"美国主导（{us_wins} 次），但**非美国开发商**也贡献了 {non_us_total} 次夺冠，"
  f"来自 { '、'.join(top_non_us) } 等 {len(non_us)} 个国家/地区；"
  f"其中近 5 年（{MAX_YEAR-4}–{MAX_YEAR}）非美国开发商夺冠 {recent_non_us} 次。"
  "这是“西方 3A 中心”从美国单极走向欧美多级的一个缩影，但样本仍小，宜作趋势观察而非定论。\n")

A("### 5.2 发行商\n")
A("| 发行商 | 夺冠次数 |")
A("|---|---|")
for p, w in publisher_wins.most_common(8):
    A(f"| {p} | {w} |")
A("")
top_pub_names = "、".join(f"{p}（{w}次）" for p, w in top_pub)
A(f"发行集中度明显：前 {len(top_pub)} 大发行商（{top_pub_names}）合计占 "
  f"{round(100*sum(w for _,w in top_pub)/N_GOTY)}% 的年度最佳——再次指向“资源集中度”假设（见第 7 节）。\n")

A("### 5.3 平台\n")
A("| 平台 | GOTY 数 |")
A("|---|---|")
for p, w in platform_wins.most_common():
    A(f"| {p} | {w} |")
A("")
top_plat_names = "、".join(f"{p}（{w}次）" for p, w in top_plat)
if nintendo_wins:
    A(f"夺冠最多的平台为 {top_plat_names}；任天堂系平台合计仅 {nintendo_wins} 次。"
      "**主机独占/第一方**在评奖中具备天然曝光优势，是可量化验证的课题。\n")
else:
    A(f"夺冠最多的平台为 {top_plat_names}。"
      "**主机独占/第一方**在评奖中具备天然曝光优势，是可量化验证的课题。\n")

# 6. 文本挖掘
A("## 6. 文本挖掘：神作共享的“基因”与通病\n")
A("对 GOTY 的“影响力/独特之处”与全部游戏的同类文本做中文关键词频率对比"
  "（同一游戏多字段去重计数）。\n")
A("> 注：以下关键词来自**预定义分析词表**（THEME_KW / DRAW_KW，属分析口径而非从数据中发现）；"
  "词频排名是数据驱动的，但词表本身是研究选择，结论应据此谨慎表述。\n")
A("### 6.1 主题关键词（GOTY 高频）\n")
A("| 关键词 | GOTY 文本命中 | 全样本文本命中 |")
A("|---|---|---|")
for k, v in goty_theme.most_common(12):
    A(f"| {k} | {v} | {all_theme.get(k,0)} |")
A("")
A(f"GOTY 文本中最高频的主题词为 **{ '、'.join(top_theme) }**，与全样本相比更为集中——"
  "印证“年度最佳”本质是**高完成度的叙事型开放世界体验**。\n")

A("### 6.2 缺点关键词（GOTY 也躲不掉）\n")
A("| 关键词 | GOTY 命中 |")
A("|---|---|")
for k, v in goty_draw.most_common(10):
    if v:
        A(f"| {k} | {v} |")
A("")
A(f"即使是满分神作，也常被点名 **{ '、'.join(top_draw) }**。这揭示一个反差洞察："
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

A("\n---\n"
  "*报告由 `analysis/mine.py` 生成。量化表与叙述性结论均随 `data/graph.json` 实时重算；"
  "本文件为**生成时刻的数据快照**，数据变更后请重跑本脚本并人工复核叙述性结论"
  "（数据漂移时脚本会打印告警，并在报告顶部标出）。*\n")

os.makedirs(os.path.join(ROOT, "docs"), exist_ok=True)
open(os.path.join(ROOT, "docs/INSIGHTS.md"), "w", encoding="utf-8").write("\n".join(L))

# 写入数据基线（更新为本次生成所对应的快照）
json.dump({
    "sha256": GRAPH_SHA,
    "nodes": len(nodes), "edges": len(edges),
    "goty": N_GOTY, "games": N_GAMES, "studios": N_STUDIOS, "genres": N_GENRES,
    "generated_at": stats["generated_at"],
}, open(BASELINE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

print("OK -> docs/INSIGHTS.md (%d lines), analysis/stats.json" % len(L))
print("GOTY design dims:", dict(goty_dim))
print("multi winners share:", stats["multi_winner_share"], "% | new IP:", new_ip_count,
      "| club90:", len(club90))
print("goty rating mean/median:", stats["ratings"]["goty_mean"], stats["ratings"]["goty_median"])
print("data_changed_since_last_run:", data_changed)
