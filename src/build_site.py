#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build site/index.html (knowledge-graph explorer) from data/graph.json.

Run from anywhere:
  python3 src/build_site.py
"""
import json, os, shutil

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

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>近20年年度最佳游戏 · 知识图谱</title>
<style>
  :root{
    --bg:#0f1115; --panel:#171a21; --panel2:#1f232c; --line:#2a2f3a;
    --txt:#e8eaed; --muted:#9aa3b2; --gold:#f5b301; --blue:#3b6ea5;
    --purple:#8e44ad; --green:#27ae60; --red:#e74c3c; --accent:#f5b301;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  header{padding:14px 20px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#171a21,#0f1115)}
  header h1{margin:0;font-size:18px;letter-spacing:.5px}
  header h1 .c{color:var(--gold)}
  .sub{color:var(--muted);font-size:12px;margin-top:4px}
  .chips{margin-top:10px;display:flex;gap:8px;flex-wrap:wrap}
  .chip{background:var(--panel2);border:1px solid var(--line);border-radius:20px;
    padding:4px 12px;font-size:12px;color:var(--muted)}
  .chip b{color:var(--txt)}
  .toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;
    padding:10px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
  .toolbar input[type=text]{background:var(--panel2);border:1px solid var(--line);
    color:var(--txt);border-radius:8px;padding:7px 10px;min-width:180px;font-size:13px}
  .toolbar select{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
    border-radius:8px;padding:7px 8px;font-size:13px;max-width:200px}
  .toolbar label{font-size:12px;color:var(--muted);display:inline-flex;align-items:center;gap:4px}
  button.btn{background:var(--panel2);border:1px solid var(--line);color:var(--txt);
    border-radius:8px;padding:7px 12px;font-size:13px;cursor:pointer}
  button.btn:hover{border-color:var(--gold)}
  button.btn.primary{background:var(--gold);color:#1a1d24;border-color:var(--gold);font-weight:600}
  .main{display:flex;height:calc(100vh - 188px);min-height:420px;position:relative}
  #graph{flex:1;height:100%;background:radial-gradient(circle at 30% 20%,#14171d,#0c0e12);position:relative}
  #loading{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;
    background:rgba(12,14,18,.82);z-index:5;color:var(--muted);font-size:13px;gap:14px}
  .spinner{width:34px;height:34px;border:3px solid var(--line);border-top-color:var(--gold);
    border-radius:50%;animation:spin 1s linear infinite}
  @keyframes spin{to{transform:rotate(360deg)}}
  #detail{width:360px;min-width:320px;max-width:42%;border-left:1px solid var(--line);
    background:var(--panel);overflow:auto;padding:16px;position:relative}
  #detail .closeBtn{position:absolute;top:10px;right:12px;cursor:pointer;color:var(--muted);
    font-size:18px;line-height:1;display:none}
  #detail h2{margin:0 0 4px;font-size:16px;padding-right:20px}
  #detail .tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:6px;
    background:var(--panel2);color:var(--muted);margin:2px 4px 2px 0}
  #detail .tag.goty{background:var(--gold);color:#1a1d24}
  dl{margin:10px 0 0}
  dt{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-top:10px}
  dd{margin:3px 0 0;font-size:13px;line-height:1.5}
  .hint{color:var(--muted);font-size:13px;line-height:1.6}
  .gamelink{display:block;padding:6px 8px;border-radius:6px;background:var(--panel2);
    margin:4px 0;font-size:13px;cursor:pointer;border:1px solid transparent}
  .gamelink:hover{border-color:var(--gold)}
  .gamelink .yr{color:var(--muted);font-size:11px;margin-right:6px}
  .legend{display:flex;gap:14px;flex-wrap:wrap;padding:8px 20px;border-top:1px solid var(--line);
    background:var(--panel);font-size:12px;color:var(--muted);align-items:center}
  .legend .grp{display:inline-flex;gap:6px;align-items:center}
  .legend .sep{border-left:1px solid var(--line);height:14px;margin:0 4px}
  .dot{width:11px;height:11px;border-radius:50%;display:inline-block}
  .nglyph{font-style:normal;font-size:14px;line-height:1;display:inline-block;width:15px;text-align:center}
  .line{width:18px;height:0;border-top:2px solid;display:inline-block}
  #tableView{display:none;padding:18px 20px;overflow:auto;height:calc(100vh - 188px)}
  table{border-collapse:collapse;width:100%;font-size:13px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}
  th{color:var(--muted);font-weight:600;position:sticky;top:0;background:var(--panel);cursor:pointer;user-select:none}
  th:hover{background:var(--panel2)}
  th.sorted{background:var(--panel2);color:var(--gold)}
  tr:hover td{background:var(--panel2);cursor:pointer}
  .star{color:var(--gold)}
  .foot{font-size:11px;color:var(--muted);padding:6px 20px}
  a{color:var(--gold)}
  @media (max-width:760px){
    .main{flex-direction:column;height:auto;min-height:0}
    #graph{height:60vh;min-height:340px}
    #detail{width:100%;min-width:0;max-width:none;border-left:none;border-top:1px solid var(--line)}
    #detail .closeBtn{display:block}
    .toolbar input[type=text]{min-width:120px;flex:1 1 140px}
  }
</style>
</head>
<body>
<header>
  <h1>🎮 近20年<span class="c">年度最佳游戏</span>知识图谱 <span style="font-size:12px;color:var(--muted)">2006–2025</span></h1>
  <div class="sub">Spike VGA / VGX（2006–2013）→ The Game Awards（2014–2025）· 点击节点查看详情，按年份 / 工作室 / 类型筛选</div>
  <div class="chips" id="chips"></div>
</header>

<div class="toolbar">
  <input type="text" id="q" placeholder="搜索游戏 / 工作室（中英文）"/>
  <label>年份 <select id="yMin"></select> – <select id="yMax"></select></label>
  <label>工作室 <select id="studio"><option value="ALL">全部</option>__STUDIO_OPTS__</select></label>
  <label>类型 <select id="genre"><option value="ALL">全部</option>__GENRE_OPTS__</select></label>
  <label><input type="checkbox" id="onlyGoty"/> 仅年度最佳</label>
  <label><input type="checkbox" id="showStudio" checked/> 工作室</label>
  <label><input type="checkbox" id="showGenre"/> 类型</label>
  <label><input type="checkbox" id="showAward" checked/> 奖项</label>
  <button class="btn" id="fit" title="适配视图">⤢ 适配</button>
  <button class="btn" id="collapseAll" title="折叠所有开发商与类型分支">⊟ 折叠全部</button>
  <button class="btn" id="expandAll" title="展开所有分支">⊞ 展开全部</button>
  <button class="btn" id="reset">重置</button>
  <button class="btn primary" id="toggleView">表格视图</button>
</div>

<div class="main">
  <div id="graph">
    <div id="loading"><div class="spinner"></div><div>正在构建知识图谱…</div></div>
  </div>
  <div id="detail">
    <span class="closeBtn" id="detailClose">×</span>
    <p class="hint">👈 在左侧力导向图中点击任意节点查看详情。<br><br>
    · <b style="color:var(--gold)">金色星形</b> = 年度最佳游戏（GOTY）<br>
    · <b style="color:var(--blue)">蓝色圆点</b> = 该工作室的其他作品<br>
    · <b style="color:var(--purple)">紫色菱形</b> = 游戏开发商<br>
    · <b style="color:var(--green)">绿色六边形</b> = 游戏类型（大=顶层原子类别，小=子类型）<br>
    · <b style="color:var(--red)">红色三角</b> = 年度大奖<br><br>
    边含义：开发商→游戏「开发了」；金色虚线「获得」；绿色虚线「属于类型」；绿色点线「类型层级」（子类型→父类型）。点击节点会自动高亮其相邻节点。<br><br>
    🖱️ <b>双击任意节点</b>可<b>折叠 / 展开</b>其分支（带金色边框 = 已折叠）；工具栏「折叠全部 / 展开全部」可一键切换；也可点详情面板里的「折叠分支」按钮。<br><br>
    <b>类型下拉</b>按顶层类别筛选；其中「开放世界 / 多人合作 / 在线」是<b>设计维度</b>（跨玩法的特征标签，可与玩法类别叠加），例如筛选「开放世界」即可看到艾尔登法环、旷野之息、天际、GTA、巫师3 等全部开放世界游戏。</p>
  </div>
</div>

<div id="tableView"></div>

<div class="legend">
  <span class="grp"><i class="nglyph" style="color:var(--gold)">★</i>年度最佳 GOTY</span>
  <span class="grp"><i class="nglyph" style="color:var(--blue)">●</i>其他作品</span>
  <span class="grp"><i class="nglyph" style="color:var(--purple)">◆</i>开发商</span>
  <span class="grp"><i class="nglyph" style="color:var(--green)">⬡</i>类型</span>
  <span class="grp"><i class="nglyph" style="color:var(--red)">▲</i>奖项</span>
  <span class="sep"></span>
  <span class="grp"><i class="line" style="border-color:#4a5160"></i>开发了</span>
  <span class="grp"><i class="line" style="border-color:var(--gold);border-top-style:dashed"></i>获得</span>
  <span class="grp"><i class="line" style="border-color:var(--green);border-top-style:dashed"></i>属于类型</span>
  <span class="grp"><i class="line" style="border-color:var(--green);border-top-style:dotted"></i>类型层级</span>
  <span class="sep"></span>
  <span class="grp"><i class="dot" style="background:#171a21;border:2px solid var(--gold)"></i>已折叠（双击展开）</span>
</div>
<div class="foot">数据来源：The Game Awards / Spike VGA 公开资料与 Metacritic 评分（综合整理，评分以 Metacritic 媒体均分为参考）。可导入 Neo4j：见 data/neo4j/ 与 docs/neo4j_tutorial.md。</div>

<script src="assets/vis-network.min.js"></script>
<script>
const GRAPH = __GRAPH_JSON__;
const DEFAULT_HINT = document.getElementById('detail').innerHTML;
const byId = {};
GRAPH.nodes.forEach(n => byId[n.id] = n);
const genreNameToId = {};
GRAPH.nodes.forEach(n => { if (n.group === 'genre') genreNameToId[n.raw.name] = n.id; });
const years = GRAPH.nodes.filter(n=>n.group==='game'||n.group==='goty')
  .map(n=>n.raw.year).filter(y=>typeof y==='number');
const yMinAll = Math.min(...years), yMaxAll = Math.max(...years);

// chips
const s = GRAPH.stats;
document.getElementById('chips').innerHTML = [
  ['年度最佳', s.goty], ['游戏总数', s.games], ['开发商', s.studios],
  ['顶层类别', s.top_genres], ['类型(含子类)', s.genres], ['奖项', s.awards], ['关系', GRAPH.edges.length]
].map(([k,v])=>`<span class="chip">${k} <b>${v}</b></span>`).join('');

// year selects
const yMinSel=document.getElementById('yMin'), yMaxSel=document.getElementById('yMax');
for(let y=yMinAll;y<=yMaxAll;y++){yMinSel.add(new Option(y,y));yMaxSel.add(new Option(y,y));}
yMinSel.value=yMinAll; yMaxSel.value=yMaxAll;

// vis-network
const groupColors = {
  goty:{background:'#f5b301',border:'#ffd54a'},
  game:{background:'#3b6ea5',border:'#5b8fd0'},
  studio:{background:'#8e44ad',border:'#b06fd0'},
  genre:{background:'#27ae60',border:'#52d68a'},
  award:{background:'#e74c3c',border:'#ff7a6a'}
};
GRAPH.nodes.forEach(n=>{
  let ob, obw, col;
  if(n.group==='genre'){
    if(n.tier1){ n.size=16; n.borderWidth=3; col={background:'#2f9e54',border:'#86efac'}; ob='#86efac'; obw=3; }
    else { n.size=9; col=groupColors.genre; ob=groupColors.genre.border; obw=2; }
  } else { col=groupColors[n.group]; ob=groupColors[n.group].border; obw=2; }
  n.color=col; n._origColor=JSON.parse(JSON.stringify(col)); n._origBorder=ob; n._origBorderW=obw;
});
const edgeColor = {DEVELOPED:'#4a5160', WON:'#f5b301', BELONGS_TO_GENRE:'#27ae60', SUBCLASS_OF:'#27ae60'};
GRAPH.edges.forEach((e,i)=>{
  e.color=edgeColor[e.type] || '#6b7280';
  if(e.type==='WON') e.dashes=true;
  else if(e.type==='BELONGS_TO_GENRE') e.dashes=[2,4];
  else if(e.type==='SUBCLASS_OF') e.dashes=[1,5];
  e.width = e.type==='WON'?2:1;
  e.id = 'e'+i;
});
const nodesDS = new vis.DataSet(GRAPH.nodes);
const edgesDS = new vis.DataSet(GRAPH.edges);
const net = new vis.Network(document.getElementById('graph'), {nodes:nodesDS,edges:edgesDS},{
  nodes:{font:{color:'#e8eaed',size:13,face:'-apple-system,sans-serif'},
    borderWidth:2,shadow:{enabled:true,size:6,color:'rgba(0,0,0,.4)'}},
  edges:{smooth:{type:'continuous'},font:{color:'#9aa3b2',size:10},selectionWidth:2},
  physics:{barnesHut:{gravitationalConstant:-9000,springLength:130,springConstant:0.03,
    damping:0.35,avoidOverlap:0.6},stabilization:{iterations:220}},
  interaction:{hover:true,tooltipDelay:120,navigationButtons:false,zoomView:true}
});
function hideLoading(){const l=document.getElementById('loading');if(l)l.style.display='none';}
net.once('stabilizationIterationsDone',()=>{net.setOptions({physics:{barnesHut:{gravitationalConstant:-9000}}});net.fit({animation:false});hideLoading();});
setTimeout(hideLoading, 4000);

function esc(x){return (x==null?'':String(x)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

// ---------- neighbor highlight ----------
let highlightActive=false;
function clearHighlight(){
  highlightActive=false;
  nodesDS.update(GRAPH.nodes.map(n=>({id:n.id,opacity:1})));
  edgesDS.update(GRAPH.edges.map(e=>({id:e.id,opacity:1})));
}
function highlightNeighbors(id){
  const nbNodes=new Set([id]); const nbEdges=new Set();
  GRAPH.edges.forEach(e=>{
    if(e.from===id){nbNodes.add(e.to);nbEdges.add(e.id);}
    if(e.to===id){nbNodes.add(e.from);nbEdges.add(e.id);}
  });
  nodesDS.update(GRAPH.nodes.map(n=>({id:n.id,opacity:nbNodes.has(n.id)?1:0.12})));
  edgesDS.update(GRAPH.edges.map(e=>({id:e.id,opacity:nbEdges.has(e.id)?1:0.12})));
  highlightActive=true;
}

// ---------- detail rendering ----------
function renderDetail(id){
  const n = byId[id]; if(!n){document.getElementById('detail').innerHTML=DEFAULT_HINT;return;}
  const d = n.raw; const el = document.getElementById('detail');
  if(n.group==='game' || n.group==='goty'){
    const r=d;
    const genreTags = (r.genres && r.genres.length ? r.genres : [r.genre]).filter(Boolean)
      .map(g=>`<span class="tag">${esc(g)}</span>`).join('');
    const tags = [`<span class="tag ${r.is_goty?'goty':''}">${r.is_goty?'★ 年度最佳游戏':'其他作品'}</span>`,
      genreTags,
      r.year?`<span class="tag">${r.year}</span>`:'',
      (r.player_rating!==''&&r.player_rating!=null)?`<span class="tag">评分 ${r.player_rating}</span>`:''].join('');
    let html = `<h2>${esc(r.title_zh)}</h2><div style="color:var(--muted);font-size:12px">${esc(r.title)}</div><div style="margin:8px 0">${tags}</div>`;
    html += `<dl>`;
    const row=(t,v)=> v?`<dt>${t}</dt><dd>${esc(v)}</dd>`:'';
    html += row('开发商', r.developer);
    html += row('发行商', r.publisher);
    html += row('平台', r.platforms);
    html += row('发售日期', r.release_date);
    html += row('玩法', r.gameplay);
    html += row('独特之处', r.unique_features);
    html += row('缺点 / 争议', r.drawbacks);
    html += row('主要奖项', r.awards);
    html += row('影响力', r.influence);
    html += row('简介', r.description);
    html += `</dl>`;
    if(r.developer_id) html += `<div style="margin-top:12px"><span class="gamelink" onclick="selectNode('${r.developer_id}',{expand:true})">查看开发商：${esc(r.developer)} 的全部作品 →</span></div>`;
    el.innerHTML = html;
  } else if(n.group==='studio'){
    let html = `<h2>${esc(d.name_zh)}</h2><div style="color:var(--muted);font-size:12px">${esc(d.name)}</div>`;
    html += `<div style="margin:8px 0">${[d.founded?`<span class="tag">成立 ${d.founded}</span>`:'',
      d.country?`<span class="tag">${esc(d.country)}</span>`:'',
      d.parent?`<span class="tag">${esc(d.parent)}</span>`:''].join('')}</div>`;
    html += `<dl>${d.hq?`<dt>总部</dt><dd>${esc(d.hq)}</dd>`:''}${d.description?`<dt>简介</dt><dd>${esc(d.description)}</dd>`:''}</dl>`;
    const gs = GRAPH.edges.filter(e=>e.type==='DEVELOPED'&&e.from===id)
      .map(e=>byId[e.to]).sort((a,b)=>(a.raw.year||0)-(b.raw.year||0));
    html += `<dt style="margin-top:14px">开发作品（${gs.length}）</dt>`;
    gs.forEach(g=>{const r=g.raw;html+=`<span class="gamelink" onclick="selectNode('${g.id}')"><span class="yr">${r.year||'—'}</span>${esc(r.title_zh)}${r.is_goty?' <span class="star">★</span>':''}</span>`;});
    el.innerHTML = html;
  } else if(n.group==='genre'){
    const gs = GRAPH.edges.filter(e=>e.type==='BELONGS_TO_GENRE'&&e.to===id).map(e=>byId[e.from]);
    const children = GRAPH.edges.filter(e=>e.type==='SUBCLASS_OF'&&e.from===id).map(e=>byId[e.to]);
    let html=`<h2>${esc(d.name)}</h2><div style="color:var(--muted);font-size:12px">类型 · ${gs.length} 款游戏 · 层级 ${d.tier||1}</div>`;
    const parts=[];
    if(d.parent){const pid=genreNameToId[d.parent]; if(pid) parts.push(`<span class="gamelink" onclick="selectNode('${pid}')">↑ 父类：${esc(d.parent)}</span>`);}
    if(children.length){
      parts.push(`<dt style="margin-top:10px">子类（${children.length}）</dt>`);
      children.sort((a,b)=>a.raw.name.localeCompare(b.raw.name,'zh')).forEach(c=>{
        parts.push(`<span class="gamelink" onclick="selectNode('${c.id}')">${esc(c.raw.name)}</span>`);});
    }
    html+=parts.join('');
    html+=`<dt style="margin-top:14px">所属游戏（${gs.length}）</dt>`;
    gs.sort((a,b)=>(a.raw.year||0)-(b.raw.year||0)).forEach(g=>{const r=g.raw;html+=`<span class="gamelink" onclick="selectNode('${g.id}')"><span class="yr">${r.year||'—'}</span>${esc(r.title_zh)}${r.is_goty?' <span class="star">★</span>':''}</span>`;});
    el.innerHTML=html;
  } else if(n.group==='award'){
    const g = byId[d.game_id];
    let html=`<h2>GOTY ${d.year}</h2><div style="color:var(--muted);font-size:12px">${esc(d.body)}</div>`;
    if(g) html+=`<div style="margin-top:10px"><span class="gamelink" onclick="selectNode('${g.id}')">🏆 ${esc(g.raw.title_zh)}</span></div>`;
    el.innerHTML=html;
  }
  el.innerHTML += detailCollapseBtn(id);
}
function selectNode(id, opts){
  if(!byId[id]) return;
  renderDetail(id);
  if(currentView==='table') toggleView();
  if(highlightActive) clearHighlight();
  if(opts && opts.expand && collapsedSet.has(id)){ collapsedSet.delete(id); applyCollapsedVisual(id); applyVisibility(); }
  net.selectNodes([id]); net.focus(id,{scale:1.1,animation:true});
  highlightNeighbors(id);
  selectedId=id;
}
net.on('click',p=>{
  if(p.nodes.length){selectNode(p.nodes[0]);}
  else {clearHighlight();net.unselectAll();renderDetail(null);selectedId=null;}
});
net.on('doubleClick',p=>{ if(p.nodes.length) toggleCollapse(p.nodes[0]); });
document.getElementById('detailClose').onclick=()=>{clearHighlight();net.unselectAll();renderDetail(null);selectedId=null;};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){clearHighlight();net.unselectAll();renderDetail(null);selectedId=null;}});

// ---------- expand / collapse ----------
let collapsedSet = new Set();
let filterVisibleSet = new Set();
let selectedId = null;
function isProtected(n){ return !n || n.group==='goty'||n.group==='studio'||n.group==='award'||(n.group==='genre'&&n.tier1); }
function computeCollapsedHidden(){
  const hidden=new Set(); let changed=true;
  while(changed){
    changed=false;
    collapsedSet.forEach(cid=>{ GRAPH.edges.forEach(e=>{
      const o = e.from===cid? e.to : (e.to===cid? e.from : null);
      if(o && !hidden.has(o) && !isProtected(byId[o])){ hidden.add(o); changed=true; }
    });});
    hidden.forEach(hid=>{ GRAPH.edges.forEach(e=>{
      const o = e.from===hid? e.to : (e.to===hid? e.from : null);
      if(o && !hidden.has(o) && !isProtected(byId[o])){ hidden.add(o); changed=true; }
    });});
  }
  return hidden;
}
function applyVisibility(){
  const hc = computeCollapsedHidden();
  nodesDS.update(GRAPH.nodes.map(n=>{
    const fv = filterVisibleSet.has(n.id);
    return {id:n.id, hidden: !fv || (hc.has(n.id) && !collapsedSet.has(n.id))};
  }));
  edgesDS.update(GRAPH.edges.map(e=>{
    const fv = filterVisibleSet.has(e.from) && filterVisibleSet.has(e.to);
    return {id:e.id, hidden: !fv || hc.has(e.from) || hc.has(e.to)};
  }));
}
function applyCollapsedVisual(id){
  const n=byId[id]; if(!n) return;
  const c=collapsedSet.has(id);
  nodesDS.update({id, borderWidth: c?4:n._origBorderW,
    color: c? {background:n._origColor.background, border:'#f5b301'} : n._origColor,
    title:(n.title||n.label)+(c?' · 已折叠，双击展开':'')});
}
function toggleCollapse(id){
  if(!byId[id]) return;
  if(collapsedSet.has(id)) collapsedSet.delete(id); else collapsedSet.add(id);
  applyCollapsedVisual(id);
  applyVisibility();
  if(selectedId) highlightNeighbors(selectedId); else clearHighlight();
}
function expandAll(){
  collapsedSet.forEach(id=>applyCollapsedVisual(id));
  collapsedSet.clear(); applyVisibility();
  if(selectedId) highlightNeighbors(selectedId); else clearHighlight();
  net.fit({animation:true});
}
function collapseAll(){
  GRAPH.nodes.forEach(n=>{ if(n.group==='studio'||n.group==='genre') collapsedSet.add(n.id); });
  collapsedSet.forEach(id=>applyCollapsedVisual(id));
  applyVisibility();
  if(selectedId) highlightNeighbors(selectedId); else clearHighlight();
  net.fit({animation:true});
}
function detailCollapseBtn(id){
  const c=collapsedSet.has(id);
  return `<div style="margin-top:12px"><button class="btn" onclick="toggleCollapse('${id}')">${c?'⊞ 展开分支':'⊟ 折叠分支'}</button></div>`;
}

// ---------- filters ----------
function applyFilters(){
  const q=(document.getElementById('q').value||'').toLowerCase().trim();
  const yMin=+document.getElementById('yMin').value, yMax=+document.getElementById('yMax').value;
  const studio=document.getElementById('studio').value;
  const genre=document.getElementById('genre').value;
  const onlyGoty=document.getElementById('onlyGoty').checked;
  const showStudio=document.getElementById('showStudio').checked;
  const showGenre=document.getElementById('showGenre').checked;
  const showAward=document.getElementById('showAward').checked;

  let focus = GRAPH.nodes.filter(n=>(n.group==='game'||n.group==='goty'));
  focus = focus.filter(n=>{
    const y=n.raw.year; if(typeof y==='number'&&(y<yMin||y>yMax)) return false;
    if(onlyGoty && n.group!=='goty') return false;
    if(genre!=='ALL' && !(n.raw.tiers||[]).includes(genre)) return false;
    if(q){const hay=(n.raw.title+' '+n.raw.title_zh+' '+(n.raw.description||'')).toLowerCase();
      if(!hay.includes(q)) return false;}
    return true;
  });
  let focusIds=new Set(focus.map(n=>n.id));
  if(studio!=='ALL'){
    const devGames=new Set(GRAPH.edges.filter(e=>e.type==='DEVELOPED'&&e.from===studio).map(e=>e.to));
    focusIds=new Set([...focusIds].filter(id=>devGames.has(id)));
  }
  const visStudios=new Set();
  GRAPH.edges.forEach(e=>{if(e.type==='DEVELOPED'&&focusIds.has(e.to)) visStudios.add(e.from);});
  const visGenres=new Set(), visAwards=new Set();
  GRAPH.edges.forEach(e=>{
    if(e.type==='BELONGS_TO_GENRE'&&focusIds.has(e.from)) visGenres.add(e.to);
    if(e.type==='WON'&&focusIds.has(e.from)) visAwards.add(e.to);
  });
  const visNodes=new Set(focusIds);
  if(showStudio) (studio!=='ALL'?visNodes.add(studio):visStudios.forEach(x=>visNodes.add(x)));
  if(showGenre){
    const gfull=new Set(visGenres);
    visGenres.forEach(gid=>{ let cur=byId[gid];
      while(cur&&cur.raw&&cur.raw.parent){ const pid=genreNameToId[cur.raw.parent]; if(!pid) break; gfull.add(pid); cur=byId[pid]; } });
    gfull.forEach(x=>visNodes.add(x));
  }
  if(showAward) visAwards.forEach(x=>visNodes.add(x));

  filterVisibleSet = visNodes;
  applyVisibility();
  // 表格视图与图谱共用同一筛选集合，筛选变化时同步刷新表格
  if(currentView==='table') buildTable();
}
['q','yMin','yMax','studio','genre','onlyGoty','showStudio','showGenre','showAward']
  .forEach(id=>{const el=document.getElementById(id);
    el.addEventListener('input',applyFilters);el.addEventListener('change',applyFilters);});
document.getElementById('fit').onclick=()=>net.fit({animation:true});
document.getElementById('reset').onclick=()=>{
  document.getElementById('q').value='';document.getElementById('yMin').value=yMinAll;
  document.getElementById('yMax').value=yMaxAll;document.getElementById('studio').value='ALL';
  document.getElementById('genre').value='ALL';document.getElementById('onlyGoty').checked=false;
  document.getElementById('showStudio').checked=true;document.getElementById('showGenre').checked=false;
  document.getElementById('showAward').checked=true;
  clearHighlight();net.unselectAll();renderDetail(null);selectedId=null;
  applyFilters();expandAll();
};
document.getElementById('collapseAll').onclick=collapseAll;
document.getElementById('expandAll').onclick=expandAll;

// ---------- table view ----------
let currentView='graph';
let tableSort={key:'year', dir:1};   // 默认按年份升序；dir=1 升序 / -1 降序
const TABLE_COLS=[
  ['year','年份'],['title_zh','游戏（中）'],['title','游戏（英）'],
  ['genre','类型'],['developer','开发商'],['rating','评分'],['goty','GOTY']
];
function _sortVal(n,key){
  const r=n.raw;
  switch(key){
    case 'year':      return (typeof r.year==='number')? r.year : null;
    case 'title_zh':  return (r.title_zh||'').toString();
    case 'title':     return (r.title||'').toString();
    case 'genre':     return (r.genres&&r.genres.length)? r.genres.join('、') : (r.genre||'');
    case 'developer': return r.developer||'';
    case 'rating':    { const v=r.player_rating; return (v===''||v==null)? null : Number(v); }
    case 'goty':      return r.is_goty?1:0;
  }
  return '';
}
function sortTable(key){
  if(tableSort.key===key){ tableSort.dir*=-1; }
  else { tableSort.key=key; tableSort.dir = (key==='rating')? -1 : 1; }  // 评分默认从高到低
  buildTable();
}
function buildTable(){
  // 仅纳入当前筛选命中的游戏节点（与图谱共用 filterVisibleSet）
  const rows=GRAPH.nodes.filter(n=>(n.group==='game'||n.group==='goty') && filterVisibleSet.has(n.id));
  const k=tableSort.key, dir=tableSort.dir;
  rows.sort((a,b)=>{
    const va=_sortVal(a,k), vb=_sortVal(b,k);
    const an=(va===null), bn=(vb===null);
    if(an&&bn) return 0;
    if(an) return 1;            // 缺失值恒排末尾
    if(bn) return -1;
    let c = (typeof va==='number'&&typeof vb==='number') ? (va-vb)
            : String(va).localeCompare(String(vb),'zh');
    return c*dir;
  });
  const head=TABLE_COLS.map(([key,label])=>{
    const active=tableSort.key===key;
    const arrow=active? (dir===1?' ▲':' ▼') : '';
    return `<th class="${active?'sorted':''}" onclick="sortTable('${key}')">${label}${arrow}</th>`;
  }).join('');
  let h=`<table><thead><tr>${head}</tr></thead><tbody>`;
  if(rows.length===0){
    h+=`<tr><td colspan="${TABLE_COLS.length}" style="text-align:center;color:#888;padding:24px">无匹配结果，请调整左侧筛选条件</td></tr>`;
  } else {
    rows.forEach(n=>{const r=n.raw;
      h+=`<tr onclick="selectNode('${n.id}')"><td>${r.year}</td><td>${esc(r.title_zh)}</td><td>${esc(r.title)}</td><td>${esc((r.genres&&r.genres.length)?r.genres.join('、'):r.genre)}</td><td>${esc(r.developer)}</td><td>${r.player_rating!==''&&r.player_rating!=null?r.player_rating:'—'}</td><td>${r.is_goty?'<span class="star">★</span>':''}</td></tr>`;});
  }
  h+=`</tbody></table>`;
  document.getElementById('tableView').innerHTML=h;
}
function toggleView(){
  currentView = currentView==='graph'?'table':'graph';
  const isTable = currentView==='table';
  // 切换整个 .main（图谱+详情）与表格视图的显隐，避免隐藏图谱后仍留空黑带
  document.querySelector('.main').style.display = isTable?'none':'flex';
  document.getElementById('tableView').style.display = isTable?'block':'none';
  document.getElementById('toggleView').textContent = isTable?'图谱视图':'表格视图';
  if(isTable){
    buildTable();
    window.scrollTo({top:0,behavior:'smooth'});
  } else {
    // 容器从 display:none 恢复后画布尺寸可能归零，重绘即可保留用户当前的缩放/平移
    requestAnimationFrame(()=>{
      if(typeof net.redraw==='function') net.redraw();
    });
  }
}
document.getElementById('toggleView').onclick=toggleView;

applyFilters();
</script>
</body>
</html>
"""

HTML = (HTML.replace("__STUDIO_OPTS__", studio_opt_html)
            .replace("__GENRE_OPTS__", genre_opt_html)
            .replace("__GRAPH_JSON__", GRAPH_JSON))

os.makedirs(ASSET_DIR, exist_ok=True)
with open(os.path.join(SITE_DIR, "index.html"), "w", encoding="utf-8") as f:
    f.write(HTML)
shutil.copyfile(VENDOR_JS, os.path.join(ASSET_DIR, "vis-network.min.js"))
print("site/index.html written, bytes:", len(HTML))
print("assets/vis-network.min.js copied:", os.path.getsize(os.path.join(ASSET_DIR, "vis-network.min.js")), "bytes")
