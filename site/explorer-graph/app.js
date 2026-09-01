// GOTY 知识图谱 · 交互式探索浏览器（独立前端，不依赖原 site/explorer）
// 仅调用后端只读端点 /api/graph/{search,node,traverse,path,communities,...}。

// API 基地址：与页面同源的相对路径 /api。
// 探索 SPA 与 API 由同一 web 容器提供（/explore 与 /api 同源），
// 不再跨容器、不再需要反向代理或跨端口直连。后端若真的不可用，会如实报错（不静默兜底）。
const API = "/api";

const GROUP_COLOR = {
  game: { color: { background: "#3b6ea5", border: "#2a4f78" } },
  goty: { color: { background: "#f5b301", border: "#b3850a" } },
  studio: { color: { background: "#27ae60", border: "#1c7a43" } },
  genre: { color: { background: "#8e44ad", border: "#5f2f78" } },
  award: { color: { background: "#e74c3c", border: "#a33225" } },
};
const REL_LABEL = {
  DEVELOPED: "开发",
  WON: "获奖",
  BELONGS_TO_GENRE: "类型",
  SUBCLASS_OF: "子类",
};
// 节点类型的用户友好名称（前端展示用，避免向用户暴露后台 group 标识）。
const GROUP_LABEL = {
  game: "游戏",
  goty: "年度最佳游戏",
  studio: "工作室",
  genre: "类型",
  award: "奖项",
};
function groupLabel(g) {
  return GROUP_LABEL[g] || g || "未知";
}

// 社区分析上色调色板：足够多（≥30）以区分多数社团；不足时按模循环。
const PALETTE = [
  "#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2",
  "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac",
  "#86bcb6", "#d37295", "#fabfd2", "#b6992d", "#499894",
  "#d7b5a6", "#79706e", "#c5b0d5", "#9d7660", "#bad7f2",
  "#e08c4d", "#6b5b95", "#c44d58", "#3b8ea5", "#8a6d3b",
  "#5c8374", "#a23e48", "#7d6608", "#3a6ea5", "#cf6679",
];

function commColor(idx) {
  return PALETTE[((idx % PALETTE.length) + PALETTE.length) % PALETTE.length];
}

// 由背景色推导更深的边框色（简单按比例压暗）。
function shade(hex) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex);
  if (!m) return "#000000";
  const num = parseInt(m[1], 16);
  const r = Math.round(((num >> 16) & 0xff) * 0.65);
  const g = Math.round(((num >> 8) & 0xff) * 0.65);
  const b = Math.round((num & 0xff) * 0.65);
  return `rgb(${r},${g},${b})`;
}

/* ---------------- 状态 ---------------- */
let current = null;     // 当前聚焦节点 id
let pathA = null;
let pathB = null;
let mode = "explore";   // explore | path

const nodes = new vis.DataSet([]);
const edges = new vis.DataSet([]);
let network = null;

/* ---------------- 工具 ---------------- */
const $ = (sel) => document.querySelector(sel);
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function fetchJSON(url) {
  // 单一同源路径：url 已是 /api/... 形式，直接请求。
  // 后端若返回非 2xx，如实抛出（含 detail），不再静默切换/兜底——错误必须可见、可定位。
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j && j.detail) msg = j.detail; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}
function displayLabel(n) { return n.label || n.id; }
function setStatus(t) { $("#status").textContent = t || ""; }

/* ---------------- 后端连通性诊断 ----------------
 * 启动时主动探测同源 /api/meta，不可达就给出醒目横幅 + 修复指引，
 * 把「沉默失败」变成「可定位的错误」。这里只做“如实诊断”，不做“静默兜底”：
 * 一旦后端真的挂了，用户看到的是明确的报错，而不是前端悄悄切到别的地址糊弄过去。*/
function showConnBanner(msg) {
  let b = document.getElementById("conn-banner");
  if (!b) {
    b = document.createElement("div");
    b.id = "conn-banner";
    b.className = "conn-banner";
    document.body.appendChild(b);
  }
  b.innerHTML = msg;
  b.style.display = "block";
}
function hideConnBanner() {
  const b = document.getElementById("conn-banner");
  if (b) b.style.display = "none";
}
// 缓存 /api/meta 结果；auth_enabled=false 表示站点处于「免登录调试模式」
let backendMeta = null;

async function probeBackend() {
  try {
    const r = await fetch(`${API}/meta`, { method: "GET", cache: "no-store" });
    if (r.ok) {
      hideConnBanner();
      setStatus(`已连接（${API}）`);
      // 缓存 meta：其中的 auth_enabled 决定本站是否为「免登录调试模式」
      // （GOTY_AUTH_ENABLED=false）。loadUser 据此跳过登录态请求。
      try { backendMeta = await r.json(); } catch (e) { backendMeta = null; }
      return true;
    }
    showConnBanner(
      `⚠ 后端返回 ${r.status} ${r.statusText}（<code>${API}/meta</code>）。` +
      `请查看 web 容器日志定位问题（如 Neo4j 未就绪时查询会如实 503）。`
    );
  } catch (e) {
    showConnBanner(
      `⚠ 无法连接后端 API（<code>${API}</code>）。请确认 web 容器已启动` +
      `（在同机执行 <code>docker-compose ps</code> 应看到 web 为 Up）；` +
      `本页与 API 同源，正常情况下无需任何跨端口/代理配置。`
    );
  }
  return false;
}

/* ---------------- 取景 / 反馈 ----------------
 * vis-network 在 nodes.clear()/add() 后不会自动重新取景，且物理布局是异步稳定过程，
 * 立刻 fit 往往取到的是节点尚未铺开的初始坐标，造成「点了没反应」的错觉。
 * 因此三层保险：① 立即 fit 一次；② 监听 stabilizationIterationsDone，物理稳定后再 fit
 * （大图关键，否则节点铺开后端点在视口外）；③ 延时兜底 fit。 */
function fitView(delay = 600) {
  if (!network) return;
  network.fit({ animation: true });
  if (network.redraw) { try { network.redraw(); } catch (_) {} }
  network.once("stabilizationIterationsDone", () => {
    if (network) { network.fit({ animation: true }); if (network.redraw) { try { network.redraw(); } catch (_) {} } }
  });
  if (delay > 0) {
    setTimeout(() => { if (network) { network.fit({ animation: true }); if (network.redraw) { try { network.redraw(); } catch (_) {} } } }, delay);
  }
}

// 显式把镜头聚焦到整图（用户要的「适应视图」按钮用）。
function focusGraph() {
  if (!network) return;
  network.fit({ animation: true });
  setStatus("已适应视图（聚焦整图）");
}

// 物理开关：大图分析完成后冻结布局（静态、稳定、易定位、不占主线程）；
// 进入探索类操作前再打开，保证新节点能正常铺开。
function setPhysics(on) {
  if (!network) return;
  network.setOptions({ physics: { enabled: !!on } });
}

// 画布加载遮罩：耗时操作（社区分析）期间显示，避免「点击后卡死」的错觉。
function showOverlay(text) {
  const o = $("#overlay");
  if (!o) return;
  const t = o.querySelector(".ov-text");
  if (t) t.textContent = text || "处理中…";
  o.classList.add("show");
}
function hideOverlay() {
  const o = $("#overlay");
  if (o) o.classList.remove("show");
}

// 高亮一批节点（新加入的种子）：加粗描边 + 金色阴影，短暂后复原。
function highlightNodes(ids, ms = 1300) {
  if (!ids || !ids.length) return;
  nodes.update(ids.map((id) => ({ id, borderWidth: 5, shadow: { enabled: true, color: "#f5b301", size: 20 } })));
  setTimeout(() => {
    if (!network) return;
    nodes.update(ids.map((id) => ({ id, borderWidth: 2, shadow: { enabled: false } })));
  }, ms);
}

// 重新绘制整图时给画布一圈高亮描边，提供「确实刷新了」的即时视觉反馈。
function flashCanvas() {
  const wrap = document.querySelector(".canvas-wrap");
  if (!wrap) return;
  wrap.classList.remove("flash");
  void wrap.offsetWidth; // 触发重排以重启动画
  wrap.classList.add("flash");
}

/* ---------------- vis-network ---------------- */
function initNetwork() {
  const container = $("#canvas");
  if (typeof vis === "undefined" || !vis || !vis.Network) {
    showConnBanner(
      "⚠ 可视化库 vis-network 未加载（<code>vis-network.min.js</code> 缺失或被浏览器拦截）。" +
      "脚本已中止，所有按钮无效。请确认该文件与 index.html 同目录，或硬刷新（Ctrl/Cmd+Shift+R）绕过缓存。"
    );
    return;
  }
  try {
    network = new vis.Network(container, { nodes, edges }, {
    groups: GROUP_COLOR,
    nodes: {
      shape: "dot",
      size: 14,
      font: { color: "#e6e9ef", size: 13 },
      borderWidth: 2,
    },
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.6 } },
      color: { color: "#46506a", highlight: "#f5b301", hover: "#8aa0c8" },
      font: { color: "#9aa4b6", size: 10, strokeWidth: 0, background: "rgba(15,18,24,0.7)" },
      smooth: { type: "dynamic" },
    },
    physics: {
      stabilization: { iterations: 180 },
      barnesHut: { gravitationalConstant: -9000, springLength: 130 },
    },
    // 触摸设备无 hover：关掉可省去无谓计算，并避免「点击后 hover 样式残留」。
    // 交互由下方 click 事件承担，功能不受影响（详情走侧栏面板，信息完整）。
    interaction: {
      hover: window.matchMedia("(hover: hover)").matches,
      tooltipDelay: 120,
    },
  });
  network.on("click", (params) => {
    if (params.nodes && params.nodes.length) selectNode(params.nodes[0]);
  });
  } catch (e) {
    showConnBanner(
      "⚠ 初始化图谱画布失败：" + esc(e && e.message ? e.message : String(e)) +
      "。可视化库可能与此浏览器不兼容，请查看控制台或换用 Chrome/Edge 最新版。"
    );
  }
}

function edgeKey(e) {
  return [e.source, e.target].sort().join("|") + "|" + e.type;
}
function upsertNode(n) {
  const data = {
    id: n.id,
    label: displayLabel(n),
    group: n.group,
    title: `${n.label || n.id}\n${n.group}`,
  };
  if (nodes.get(n.id)) nodes.update(data);
  else nodes.add(data);
}
function upsertEdge(e) {
  const k = edgeKey(e);
  if (edges.get(k)) return;
  // 投影图的边带 weight（共享异类邻居数）：边越粗=两个同类节点越「相似」。
  const w = e.weight != null ? Number(e.weight) : null;
  const edge = {
    id: k,
    from: e.source,
    to: e.target,
    label: REL_LABEL[e.type] || e.type,
    title: e.type,
  };
  if (w != null && w > 0) {
    edge.width = Math.max(1, Math.min(8, 1 + Math.log2(w + 1)));
    edge.title = `${e.type}（共享属性 ${w}）`;
  }
  edges.add(edge);
}
function upsertGraph(sub) {
  if (!sub) return false;
  // seed 等场景 center 可能为 null（仅给节点+关系），但子图本身有效
  if (sub.center) upsertNode(sub.center);
  (sub.nodes || []).forEach(upsertNode);
  (sub.edges || []).forEach(upsertEdge);
  return true;
}

/* ---------------- 搜索 ---------------- */
function hideSearch() {
  $("#search-results").style.display = "none";
}

async function doSearch() {
  const q = $("#search-input").value.trim();
  if (!q) { hideSearch(); return; }
  setStatus("搜索中…");
  try {
    const data = await fetchJSON(`${API}/graph/search?q=${encodeURIComponent(q)}&limit=20`);
    const box = $("#search-results");
    box.innerHTML = "";
    if (!data.results || !data.results.length) {
      box.innerHTML = `<div class="hint">无匹配结果。</div>`;
      box.style.display = "block";
      setStatus("");
      return;
    }
    for (const n of data.results) {
      const item = document.createElement("div");
      item.className = "result-item";
      item.innerHTML =
        `<div class="ri-label">${esc(displayLabel(n))}</div>` +
        `<div class="ri-group">${esc(n.group)} · ${esc(n.id)}</div>`;
      item.addEventListener("click", () => {
        selectNode(n.id);
        if (network) network.focus(n.id, { animation: true, scale: 1.1 });
        hideSearch();
      });
      box.appendChild(item);
    }
    box.style.display = "block";
    setStatus(`找到 ${data.results.length} 个匹配`);
  } catch (e) {
    setStatus("搜索失败：" + e.message);
  }
}

/* ---------------- 节点详情 + 自动展开 ---------------- */
async function selectNode(id) {
  current = id;
  setPhysics(true); // 若处于社区分析的冻结态，进入探索需重新打开物理以铺开新节点
  setStatus("加载节点…");
  try {
    const n = await fetchJSON(`${API}/graph/node/${encodeURIComponent(id)}`);
    renderDetail(n);
  } catch (e) {
    setStatus("节点详情失败：" + e.message);
  }
  // 选中即展开 1 跳，让画布有内容
  await expandFrom(id, 1, collectTypes(), true);
  if (network) network.focus(id, { animation: true, scale: 1.05 });
}

function renderDetail(n) {
  const d = $("#detail");
  const summary = n.summary || {};
  const rows = Object.entries(summary)
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`)
    .join("");
  d.className = "detail";
  d.innerHTML =
    `<div class="d-label">${esc(n.label || n.id)}</div>` +
    `<div class="d-group">${esc(groupLabel(n.group))}</div>` +
    (rows ? `<dl>${rows}</dl>` : `<div class="hint">无更多属性</div>`);
}

function collectTypes() {
  return [...document.querySelectorAll(".rel-type:checked")].map((c) => c.value);
}

async function expandFrom(id, hops, types, merge) {
  if (!id) { setStatus("请先选择一个节点"); return; }
  if (merge) { mode = "explore"; }
  setPhysics(true);
  setStatus("展开邻居…");
  try {
    const params = new URLSearchParams({ start: id, hops: String(hops) });
    if (types && types.length) params.set("types", types.join(","));
    const data = await fetchJSON(`${API}/graph/traverse?${params}`);
    if (!data.center) {
      setStatus("节点未找到");
      return;
    }
    upsertGraph(data);
    setStatus(`已展开（${data.nodes ? data.nodes.length : 0} 个邻居）`);
  } catch (e) {
    setStatus("展开失败：" + e.message);
  }
}

/* ---------------- 最短路径 ---------------- */
function setPathNode(side, node) {
  // node: {id,label,group}。同时更新变量、文字标签与搜索框，保证三处一致。
  if (side === "a") {
    pathA = node.id;
    $("#path-a").textContent = displayLabel(node);
    const inp = $("#path-a-input");
    if (inp && document.activeElement !== inp) inp.value = displayLabel(node);
  } else {
    pathB = node.id;
    $("#path-b").textContent = displayLabel(node);
    const inp = $("#path-b-input");
    if (inp && document.activeElement !== inp) inp.value = displayLabel(node);
  }
}
function updatePathPickLabels() {
  $("#path-a").textContent = pathA ? pathA : "未选";
  $("#path-b").textContent = pathB ? pathB : "未选";
}

// 节点选择器：输入即搜，下拉联想；点选项即设为起点/终点。
function makePicker(inputId, resultsId, side) {
  const input = $("#" + inputId);
  const box = $("#" + resultsId);
  if (!input || !box) return;
  input.addEventListener("input", async () => {
    const q = input.value.trim();
    if (!q) { box.style.display = "none"; return; }
    setStatus("搜索节点…");
    try {
      const data = await fetchJSON(`${API}/graph/search?q=${encodeURIComponent(q)}&limit=8`);
      box.innerHTML = "";
      const res = data.results || [];
      if (!res.length) { box.style.display = "none"; setStatus(""); return; }
      for (const n of res) {
        const item = document.createElement("div");
        item.className = "result-item";
        item.innerHTML =
          `<div class="ri-label">${esc(displayLabel(n))}</div>` +
          `<div class="ri-group">${esc(n.group)} · ${esc(n.id)}</div>`;
        item.addEventListener("mousedown", (e) => {
          e.preventDefault(); // 先阻止 blur，保证点击生效
          setPathNode(side, n);
          box.style.display = "none";
          setStatus("");
        });
        box.appendChild(item);
      }
      box.style.display = "block";
    } catch (e) {
      // 不能只隐藏下拉：后端报错或断网时，用户只看到「搜不出东西」而无法判断原因。
      box.style.display = "none";
      setStatus("节点搜索失败：" + e.message);
    }
  });
  input.addEventListener("blur", () => setTimeout(() => { box.style.display = "none"; }, 150));
}

function swapPath() {
  const t = pathA; pathA = pathB; pathB = t;
  // 交换显示
  const la = $("#path-a"), lb = $("#path-b");
  const ta = la.textContent, tb = lb.textContent;
  la.textContent = tb; lb.textContent = ta;
  const ia = $("#path-a-input"), ib = $("#path-b-input");
  const va = ia.value, vb = ib.value;
  ia.value = vb; ib.value = va;
  setStatus("已交换起点与终点");
}
async function computePath() {
  if (!pathA || !pathB) { setStatus("请先设置起点与终点"); return; }
  if (pathA === pathB) { setStatus("起点与终点相同"); return; }
  setPhysics(true);
  setStatus("计算最短路径…");
  try {
    const data = await fetchJSON(
      `${API}/graph/path?a=${encodeURIComponent(pathA)}&b=${encodeURIComponent(pathB)}`
    );
    if (!data.nodes || !data.nodes.length) {
      setStatus("两节点之间无路径");
      renderPathResult(null, 0);
      return;
    }
    // 路径模式：清空画布，仅展示路径
    mode = "path";
    nodes.clear();
    edges.clear();
    data.nodes.forEach(upsertNode);
    data.edges.forEach((e) => {
      upsertEdge(e);
      // 高亮路径边：金色、加粗，与背景边区分
      edges.update({
        id: edgeKey(e),
        color: { color: "#f5b301", highlight: "#f5b301", hover: "#f5b301" },
        width: 3,
      });
    });
    // 高亮路径节点：金色描边 + 阴影
    const seq = data.nodes.map((n) => n.id);
    nodes.update(
      seq.map((id) => ({ id, borderWidth: 4, shadow: { enabled: true, color: "#f5b301", size: 18 } }))
    );
    fitView();
    flashCanvas();
    setStatus(`最短路径：共 ${seq.length} 个节点、${data.length ?? data.edges.length} 步`);
    renderPathResult(seq, data.length ?? data.edges.length);
  } catch (e) {
    setStatus("路径计算失败：" + e.message);
    renderPathResult(null, 0);
  }
}

// 在侧栏展示路径文字链（A → B → C…），每个节点名可点击聚焦画布。
function renderPathResult(seq, length) {
  const el = $("#path-result");
  if (!seq || !seq.length) { el.innerHTML = ""; return; }
  const parts = seq.map((id) => {
    const n = nodes.get(id);
    const label = n ? displayLabel(n) : id;
    return `<span class="pr-node" data-id="${esc(id)}" title="${esc(label)}">${esc(label)}</span>`;
  });
  el.innerHTML =
    `<div class="pr-head">最短路径（${length} 步 · ${seq.length} 个节点）</div>` +
    `<div class="pr-chain">${parts.join('<span class="pr-arrow">→</span>')}</div>`;
  el.querySelectorAll(".pr-node").forEach((s) => {
    s.addEventListener("click", () => {
      const id = s.getAttribute("data-id");
      if (network) network.focus(id, { animation: true, scale: 1.1 });
    });
  });
}

/* ---------------- 种子渲染 ---------------- */
// 进入页面默认只铺一小批（limit=12），与「渲染种子」按钮的默认 20 区分开，
// 这样用户点一次按钮就会明显看到节点增多 + 重新取景，避免「点了没反应」。
async function loadDefaultSeed() {
  setPhysics(true);
  setStatus("加载默认图谱（GOTY 获奖作品）…");
  try {
    const data = await fetchJSON(`${API}/graph/seed?group=goty&limit=12&hops=1`);
    nodes.clear(); edges.clear();
    upsertGraph(data);
    fitView();
    flashCanvas();
    setStatus(`已展示 GOTY 获奖作品及其关联（${data.nodes.length} 个节点）`);
  } catch (e) {
    setStatus("默认图谱加载失败：" + e.message);
  }
}

// 「渲染种子」= 按类别(group)+标签(tags)筛选一批节点作为探索起点：
// 调用新端点 /api/graph/filter，清空并渲染筛选结果（明确的「起点」语义，点击必有可见变化）。
async function renderSeedFilter() {
  let group = $("#seed-group").value;
  if (group === "all") group = null;
  const tags = Array.from($("#seed-tags").selectedOptions).map((o) => o.value);
  // 标签仅对游戏类生效：选了标签但类别不是 goty/game 时，放宽到「全部游戏」避免空结果。
  if (tags.length && group && group !== "goty" && group !== "game") group = null;
  const hops = parseInt($("#seed-hops").value, 10) || 0;
  mode = "explore";
  setPhysics(true);
  const params = new URLSearchParams({ hops: String(hops), limit: "200" });
  if (group) params.set("group", group);
  if (tags.length) params.set("tags", tags.join(","));
  setStatus("渲染种子（按筛选展示中）…");
  try {
    const data = await fetchJSON(`${API}/graph/filter?${params}`);
    nodes.clear(); edges.clear();
    upsertGraph(data);
    fitView();
    flashCanvas();
    const grpTxt = group || "全部";
    const tagTxt = tags.length ? tags.join("、") : "（无标签）";
    setStatus(`已按「类别=${grpTxt} · 标签=${tagTxt}」展示 ${data.nodes.length} 个节点、${data.edges.length} 条关系。点任意节点可继续展开探索。`);
  } catch (e) {
    setStatus("渲染种子失败：" + e.message);
  }
}

// 填充「标签」多选框：取自类型节点（/api/graph/list?group=genre）。
async function loadTagOptions() {
  try {
    const data = await fetchJSON(`${API}/graph/list?group=genre&limit=100`);
    const sel = $("#seed-tags");
    sel.innerHTML = "";
    (data.items || []).forEach((g) => {
      const opt = document.createElement("option");
      opt.value = g.label;
      opt.textContent = g.label;
      sel.appendChild(opt);
    });
  } catch (e) {
    setStatus("标签列表加载失败：" + e.message);
  }
}

/* ---------------- 社区分析 ---------------- */
// 算法目录（来自 /api/graph/communities/meta），前端据此动态渲染下拉与参数表单，不再硬编码。
let ALGO_META = {};
let SCOPE_META = {};

// 教育性动画播放器状态
const commAnim = { frames: [], idx: 0, playing: false, timer: null, finalAssign: {}, data: null };
let lastHighlightedEdgeKeys = new Set();

// 启动时填充算法下拉（含中文说明 / 是否支持动画 / 依赖是否就绪）。
async function loadCommunityAlgorithms() {
  const sel = $("#comm-algo");
  try {
    const data = await fetchJSON(`${API}/graph/communities/meta`);
    const algos = data.algorithms || [];
    ALGO_META = {};
    sel.innerHTML = "";
    for (const a of algos) {
      ALGO_META[a.name] = a;
      const opt = document.createElement("option");
      opt.value = a.name;
      let label = a.display_name;
      if (!a.available) {
        const dep = a.optional_dependency || "额外依赖";
        label += `（需安装 ${dep}）`;
        opt.disabled = true;
      }
      opt.textContent = label;
      sel.appendChild(opt);
    }
    const firstAvail = algos.find((a) => a.available);
    if (firstAvail) sel.value = firstAvail.name;
    // 分析范围（异质 vs 同类投影）下拉
    populateScopeSelect(data.scopes || []);
    $("#comm-btn").disabled = false;
    renderCommParams();
  } catch (e) {
    // 兜底：目录加载失败时至少保留两个零依赖核心算法，避免界面卡死。
    sel.innerHTML = "";
    for (const [v, t] of [["modularity", "模块度最大化（贪心）"], ["label_propagation", "标签传播（LPA）"]]) {
      const opt = document.createElement("option");
      opt.value = v; opt.textContent = t; sel.appendChild(opt);
    }
    ALGO_META = {
      modularity: { name: "modularity", display_name: "模块度最大化（贪心）", supports_animation: true, available: true,
        params_schema: [{ name: "resolution", label: "社团粒度 (resolution)", type: "float", default: 1.0, min: 0.1, max: 5.0, step: 0.1, help: "越大社团越细碎；越小社团越聚合。" }], blurb: "最经典的凝聚式算法：直观展示「小团如何一步步并成大社区」，Q 值单调递增。" },
      label_propagation: { name: "label_propagation", display_name: "标签传播（LPA）", supports_animation: true, available: true, params_schema: [], blurb: "近乎线性的高效算法：动画能直观看到「颜色（标签）如何一层层淹没整张图」。" },
    };
    $("#comm-btn").disabled = false;
    renderCommParams();
    setStatus("社区算法目录加载失败，已回退到内置核心算法：" + e.message);
  }
}

// 启动时填充「分析范围」下拉（全图混合 / 各类型单向投影），并展示该范围的物理含义说明。
function populateScopeSelect(scopes) {
  const sel = $("#comm-scope");
  SCOPE_META = {};
  sel.innerHTML = "";
  for (const s of scopes) {
    SCOPE_META[s.id] = s;
    const opt = document.createElement("option");
    opt.value = s.id;
    opt.textContent = s.label;
    sel.appendChild(opt);
  }
  if (!sel.value) sel.value = "all";
  const upd = () => {
    $("#comm-scope-blurb").textContent = (SCOPE_META[sel.value] || {}).blurb || "";
  };
  sel.onchange = upd;
  upd();
}

// 产品设计：点击后**立即**盖加载遮罩（spinner），布局在后台进行；稳定后取景并**冻结物理**
// （静态大图：主线程不再持续抖动/卡顿，元素稳定便于定位）。支持「演示算法过程」动画模式。
async function loadCommunities() {
  const btn = $("#comm-btn");
  const algo = $("#comm-algo").value;
  const meta = ALGO_META[algo];
  if (!algo || !meta) { setStatus("请先选择社区分析算法"); return; }
  if (!meta.available) { setStatus(`算法「${meta.display_name}」需要安装依赖：${meta.optional_dependency}`); return; }
  const wantsAnimate = meta.supports_animation && $("#comm-animate").checked;
  const params = commParamValue();
  const scope = $("#comm-scope").value || "all";
  const scopeLabel = (SCOPE_META[scope] || {}).label || scope;
  mode = "communities";
  stopCommunityAnimation(); // 任何进行中的动画先停
  if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }
  showOverlay(`正在分析社区结构（${scopeLabel}，布局中）…`);
  setStatus(`社区分析中（${scopeLabel}，正在布局）…`);
  try {
    const q = new URLSearchParams({ algorithm: algo, scope, animate: wantsAnimate ? "true" : "false" });
    for (const [k, v] of Object.entries(params)) {
      if (v !== null && v !== undefined) q.set(k, String(v));
    }
    const data = await fetchJSON(`${API}/graph/communities?${q}`);
    nodes.clear(); edges.clear();
    const nodeViews = data.nodes || [];
    for (const n of nodeViews) {
      // 先只建节点（不赋色）：动画/静态上色由后续统一处理
      upsertNode({ id: n.id, label: n.label, group: n.group });
    }
    (data.edges || []).forEach(upsertEdge);
    setPhysics(true); // 重新打开物理，确保大量节点铺开后触发 stabilizationIterationsDone
    // 优先用契约顶层下发的 assignment；老后端没有该字段时回退到从 members 派生。
    const assign = (data.assignment && Object.keys(data.assignment).length)
      ? data.assignment
      : buildAssignmentFromCommunities(data.communities || []);
    renderCommunityList(data.communities || []);
    const nComm = (data.communities || []).length;
    setStatus(`已构建社区分析图（${meta.display_name} · ${scopeLabel}）：共 ${nComm} 个社团、${nodeViews.length} 个节点。${wantsAnimate ? "正在播放算法过程…" : "已按社团上色并冻结布局；点列表项查看社团包含哪些节点。"}`);

    const finishBuild = () => {
      if (network) network.fit({ animation: true });
      setPhysics(false); // 冻结，元素稳定、易定位
      hideOverlay();
      if (btn) { btn.disabled = false; btn.textContent = "运行社区分析"; }
    };

    if (wantsAnimate && data.frames && data.frames.length) {
      startCommunityAnimation(data.frames, assign, data);
      let done = false;
      const fin = () => { if (done) return; done = true; finishBuild(); };
      if (network) network.once("stabilizationIterationsDone", fin);
      setTimeout(fin, 8000); // 兜底：稳定事件未触发也能收尾
    } else {
      applyAssignment(assign, {});
      let done = false;
      const fin = () => { if (done) return; done = true; finishBuild(); };
      if (network) network.once("stabilizationIterationsDone", fin);
      setTimeout(fin, 6000);
    }
  } catch (e) {
    hideOverlay();
    stopCommunityAnimation();
    setStatus("社区分析失败：" + e.message);
    if (btn) { btn.disabled = false; btn.textContent = "运行社区分析"; }
  }
}

// 由后端返回的 communities（每项含 members 列表）重建 {node_id: community_id} 归属表。
// 后端未单独下发顶层 assignment，这里从 communities 派生，静态上色与动画末态都以此为准。
function buildAssignmentFromCommunities(communities) {
  const a = {};
  for (const c of communities || []) {
    for (const m of (c.members || [])) a[m] = c.id;
  }
  return a;
}

// 根据社团划分（{node_id: community_id}）给画布重新上色；可选高亮被切断的边（分裂式算法）。
function applyAssignment(assign, opts) {
  opts = opts || {};
  const updates = [];
  for (const [id, c] of Object.entries(assign)) {
    if (!nodes.get(id)) continue;
    const color = commColor(c);
    updates.push({ id, color: { background: color, border: shade(color) } });
  }
  if (updates.length) nodes.update(updates);
  applyHighlights(opts.highlightEdges);
}

// 高亮「桥边」（被切断的边）：红色加粗 + 阴影；其余复位。按端点匹配（边可能含类型）。
function applyHighlights(highlightEdges) {
  const want = new Set();
  if (highlightEdges) {
    for (const [u, v] of highlightEdges) want.add([u, v].sort().join("|"));
  }
  const toReset = [];
  for (const ek of lastHighlightedEdgeKeys) {
    if (!want.has(ek)) {
      toReset.push({ id: ek, color: { color: "#46506a", highlight: "#f5b301", hover: "#8aa0c8" }, width: 1, shadow: { enabled: false } });
    }
  }
  const toHi = [];
  if (highlightEdges && highlightEdges.length) {
    edges.forEach((e) => {
      const ek = [e.from, e.to].sort().join("|");
      if (want.has(ek)) {
        toHi.push({ id: e.id, color: { color: "#e74c3c", highlight: "#ff6b5b", hover: "#ff6b5b" }, width: 3, shadow: { enabled: true, color: "#e74c3c", size: 12 } });
      }
    });
  }
  if (toReset.length) edges.update(toReset);
  if (toHi.length) edges.update(toHi);
  lastHighlightedEdgeKeys = want;
}

// 社区分析的算法参数：随「算法」下拉从 params_schema 动态生成表单（float/int 滑块、bool 勾选）。
function renderCommParams() {
  const algo = $("#comm-algo").value;
  const meta = ALGO_META[algo];
  const box = $("#comm-params");
  box.innerHTML = "";
  $("#comm-blurb").textContent = meta ? (meta.blurb || "") : "";
  const animWrap = $("#comm-anim-wrap");
  if (meta && meta.supports_animation) animWrap.style.display = "";
  else { animWrap.style.display = "none"; const cb = $("#comm-animate"); if (cb) cb.checked = false; }
  const schema = (meta && meta.params_schema) || [];
  if (!schema.length) {
    const p = document.createElement("p");
    p.className = "hint"; p.style.margin = "6px 0 0";
    p.textContent = "该算法无额外参数。";
    box.appendChild(p);
    return;
  }
  for (const sp of schema) {
    if (sp.type === "bool") {
      const lbl = document.createElement("label");
      lbl.className = "row checkbox-row";
      lbl.innerHTML = `<input type="checkbox" data-param="${esc(sp.name)}" ${sp.default ? "checked" : ""}/> ${esc(sp.label)}`;
      box.appendChild(lbl);
      if (sp.help) { const hp = document.createElement("p"); hp.className = "hint"; hp.style.margin = "2px 0 8px"; hp.textContent = sp.help; box.appendChild(hp); }
      continue;
    }
    const wrap = document.createElement("div");
    wrap.className = "param-row";
    const def = sp.default != null ? String(sp.default) : "";
    const min = sp.min != null ? `min="${sp.min}"` : "";
    const max = sp.max != null ? `max="${sp.max}"` : "";
    const step = sp.step != null ? `step="${sp.step}"` : "";
    const useRange = sp.min != null && sp.max != null;
    if (useRange) {
      wrap.innerHTML =
        `<label class="row">${esc(sp.label)} <span class="pid param-val" data-for="${esc(sp.name)}">${def || "—"}</span></label>` +
        `<input class="slider" type="range" data-param="${esc(sp.name)}" data-kind="${esc(sp.type)}" value="${def}" ${min} ${max} ${step} />` +
        (sp.help ? `<p class="hint" style="margin:2px 0 8px">${esc(sp.help)}</p>` : "");
    } else {
      // 无 min/max 边界的参数（如可选随机种子）：用数字输入框，留空即「不传 / 随机」。
      const ph = sp.default == null ? "留空=默认/随机" : "";
      wrap.innerHTML =
        `<label class="row">${esc(sp.label)} <span class="pid param-val" data-for="${esc(sp.name)}">${def || "—"}</span></label>` +
        `<input class="num-in" type="number" data-param="${esc(sp.name)}" data-kind="${esc(sp.type)}" value="${def}" placeholder="${ph}" ${step} />` +
        (sp.help ? `<p class="hint" style="margin:2px 0 8px">${esc(sp.help)}</p>` : "");
    }
    box.appendChild(wrap);
    const inp = wrap.querySelector("input");
    inp.addEventListener("input", () => {
      const span = wrap.querySelector(`[data-for="${sp.name}"]`);
      if (span) span.textContent = inp.value === "" ? "—" : inp.value;
    });
  }
}

// 收集当前参数表单的值（float/int 自动转类型；空值跳过；bool 取勾选）。
function commParamValue() {
  const out = {};
  document.querySelectorAll("#comm-params [data-param]").forEach((el) => {
    const name = el.getAttribute("data-param");
    if (el.type === "checkbox") { out[name] = el.checked; return; }
    const kind = el.getAttribute("data-kind");
    const raw = el.value;
    if (raw === "" || raw == null) return;
    out[name] = kind === "int" ? parseInt(raw, 10) : kind === "float" ? parseFloat(raw) : raw;
  });
  return out;
}

/* ---------------- 社区分析：教育性动画播放器 ---------------- */
// 在图谱上按帧重着色，配合步骤文字解说，帮助直观理解算法原理（凝聚合并 / 标签扩散 / 边分裂）。
function startCommunityAnimation(frames, finalAssign, data) {
  commAnim.frames = frames;
  commAnim.finalAssign = finalAssign || {};
  commAnim.data = data;
  commAnim.idx = 0;
  const panel = $("#comm-anim");
  if (panel) panel.style.display = "";
  const seek = $("#comm-anim-seek");
  seek.min = "0"; seek.max = String(frames.length - 1); seek.value = "0";
  const sp = $("#comm-anim-play");
  if (sp) sp.textContent = "暂停";
  commAnim.playing = true;
  renderAnimFrame(0);
  scheduleAnimTick();
}

function scheduleAnimTick() {
  clearTimeout(commAnim.timer);
  const speedSel = $("#comm-anim-speed");
  const speed = speedSel ? (parseFloat(speedSel.value) || 1) : 1;
  commAnim.timer = setTimeout(commAnimTick, 950 / speed);
}

function commAnimTick() {
  if (!commAnim.playing) return;
  if (commAnim.idx >= commAnim.frames.length - 1) {
    commAnim.idx = commAnim.frames.length - 1;
    renderAnimFrame(commAnim.idx);
    commAnim.playing = false;
    const sp = $("#comm-anim-play"); if (sp) sp.textContent = "重播";
    setStatus("算法过程演示完成。已显示最终社团划分；点列表项查看社团包含哪些节点。");
    return;
  }
  commAnim.idx += 1;
  renderAnimFrame(commAnim.idx);
  scheduleAnimTick();
}

function renderAnimFrame(i) {
  const f = commAnim.frames[i];
  if (!f) return;
  applyAssignment(f.assignment || {}, { highlightEdges: f.highlight_edges });
  const seek = $("#comm-anim-seek"); if (seek) seek.value = String(i);
  const total = commAnim.frames.length;
  // 指标文案取自后端契约的语义标签，不再硬编码「模块度」：
  // 换算法库后指标可能是 codelength（越小越好）等，标签由后端给出，前端原样渲染。
  const label = f.metric_label || "模块度 Q";
  const qtxt = (f.metric != null) ? ` · ${label}=${Number(f.metric).toFixed(3)}` : "";
  $("#comm-anim-step").textContent = `步骤 ${i + 1} / ${total}${qtxt}`;
  $("#comm-anim-desc").textContent = f.description || "";
}

function commAnimPlayPause() {
  const last = commAnim.frames.length - 1;
  if (!commAnim.frames.length) return;
  if (commAnim.idx >= last && !commAnim.playing) {
    // 已到末帧 → 重播
    commAnim.idx = 0; commAnim.playing = true;
    const sp = $("#comm-anim-play"); if (sp) sp.textContent = "暂停";
    renderAnimFrame(0); scheduleAnimTick();
    return;
  }
  commAnim.playing = !commAnim.playing;
  const sp = $("#comm-anim-play"); if (sp) sp.textContent = commAnim.playing ? "暂停" : "播放";
  if (commAnim.playing) scheduleAnimTick();
  else clearTimeout(commAnim.timer);
}

function commAnimSeek(i) {
  commAnim.idx = i;
  renderAnimFrame(i);
  const last = commAnim.frames.length - 1;
  const sp = $("#comm-anim-play");
  if (i >= last) { commAnim.playing = false; if (sp) sp.textContent = "重播"; }
  else { commAnim.playing = false; if (sp) sp.textContent = "播放"; }
}

function stopCommunityAnimation() {
  clearTimeout(commAnim.timer);
  commAnim.playing = false;
  commAnim.frames = [];
  const panel = $("#comm-anim");
  if (panel) panel.style.display = "none";
  applyHighlights(null);
}

function renderCommunityList(communities) {
  const box = $("#comm-list");
  box.innerHTML = "";
  if (!communities.length) {
    box.innerHTML = `<div class="hint">未识别出社团。</div>`;
    return;
  }
  communities.forEach((c) => {
    const item = document.createElement("div");
    item.className = "comm-item";
    const color = commColor(c.id);
    item.innerHTML =
      `<span class="comm-dot" style="background:${color}"></span>` +
      `<span class="comm-name">社团 #${c.id}</span>` +
      `<span class="comm-size">${c.size} 个节点</span>`;
    item.addEventListener("click", () => openCommPanel(c));
    box.appendChild(item);
  });
}

// 点社团列表项：在画布右侧展开成员表格（不遮挡图谱），并高亮该社团的全部节点。
function openCommPanel(c) {
  const panel = $("#comm-panel");
  const title = $("#comm-panel-title");
  const meta = $("#comm-panel-meta");
  const body = $("#comm-panel-body");
  const color = commColor(c.id);
  title.innerHTML = `<span class="comm-dot" style="background:${color};display:inline-block;vertical-align:middle;margin-right:6px"></span>社团 #${c.id} · 节点列表`;
  meta.textContent = `共 ${c.size} 个节点`;
  body.innerHTML = "";
  const members = c.members || [];
  if (!members.length) {
    body.innerHTML = `<tr><td colspan="2" class="hint">该社团无节点</td></tr>`;
  }
  for (const id of members) {
    const n = nodes.get(id);
    const label = n ? displayLabel(n) : id;
    const group = n ? (n.group || "") : "";
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${esc(label)}</td><td>${esc(groupLabel(group))}</td>`;
    tr.addEventListener("click", () => focusCommunityMember(id));
    body.appendChild(tr);
  }
  panel.classList.add("show");
  panel.setAttribute("aria-hidden", "false");
  const wrap = document.querySelector(".canvas-wrap");
  if (wrap) wrap.classList.add("panel-open");
  highlightCommunity(c);
}

function closeCommPanel() {
  const panel = $("#comm-panel");
  panel.classList.remove("show");
  panel.setAttribute("aria-hidden", "true");
  const wrap = document.querySelector(".canvas-wrap");
  if (wrap) wrap.classList.remove("panel-open");
}

// 跟踪当前被高亮（金色描边）的节点，便于切换社区/重跑影响力时清掉旧高亮，避免叠加残留。
let highlightedNodeIds = new Set();
function clearGraphHighlights() {
  for (const id of highlightedNodeIds) {
    const n = nodes.get(id);
    if (!n) continue;
    const bg = (n.color && n.color.background) || "#888";
    nodes.update({ id, borderWidth: 2, color: Object.assign({}, n.color, { border: shade(bg) }) });
  }
  highlightedNodeIds = new Set();
}

// 在画布上高亮整个社团：保留社团配色，仅给成员节点加金色描边，并聚焦到它们。
function highlightCommunity(c) {
  clearGraphHighlights();
  const ids = (c.members || []).filter((id) => nodes.get(id));
  if (!ids.length) { setStatus("该社团无可见节点（请先运行社区分析）"); return; }
  for (const id of ids) {
    const n = nodes.get(id);
    nodes.update({
      id,
      borderWidth: 3,
      color: Object.assign({}, n.color, { border: "#f5b301" }),
    });
    highlightedNodeIds.add(id);
  }
  if (network) network.fit({ nodes: ids, animation: true });
  setStatus(`已高亮社团 #${c.id}（${ids.length} 个节点，金色边框）。点右侧表格行可在画布聚焦单个节点。`);
}

// 点表格行：聚焦单个节点（不展开邻居，避免打乱社团视图），保持面板开启。
function focusCommunityMember(id) {
  if (!nodes.get(id)) return;
  current = id;
  if (network) network.focus(id, { animation: true, scale: 1.15 });
  // 不能空 catch：请求失败时详情面板会停留在上一个节点的内容，用户以为看到的是
  // 当前节点（脏数据）且无任何提示。
  fetchJSON(`${API}/graph/node/${encodeURIComponent(id)}`)
    .then(renderDetail)
    .catch((e) => setStatus("节点详情加载失败：" + e.message));
  setStatus(`已聚焦：${displayLabel(nodes.get(id))}`);
}

/* ---------------- 网络影响力（中心性） ---------------- */
const INF_METRIC_NOTE = {
  pagerank: "PageRank：不仅看连接数量，还看连接对象的“重要性”——被大奖、大厂环绕的节点得分更高，更能反映真实话语权。",
  degree: "度数中心性：直接相连的节点越多越重要，最直观（如最多产的工作室、涵盖游戏最多的类型）。",
  betweenness: "中介中心性：位于不同群体之间的“桥梁”节点；去掉它，很多社团将彼此失联——揭示图谱的结构性枢纽。",
};
function updateInfMetricNote() {
  const m = $("#inf-metric").value;
  $("#inf-metric-note").textContent = INF_METRIC_NOTE[m] || "";
}

async function loadInfluence() {
  const btn = $("#inf-btn");
  const metric = $("#inf-metric").value;
  const topN = parseInt($("#inf-topn").value, 10) || 20;
  const group = $("#inf-group").value || null;
  if (btn) { btn.disabled = true; btn.textContent = "分析中…"; }
  setStatus("计算网络影响力…");
  try {
    const params = new URLSearchParams({ metric, top_n: String(topN) });
    if (group) params.set("group", group);
    const data = await fetchJSON(`${API}/graph/influence?${params}`);
    renderInfluence(data);
    applyInfluenceHighlight(data);
    const mlabel = { pagerank: "PageRank", degree: "度数中心性", betweenness: "中介中心性" }[data.metric] || data.metric;
    setStatus(`影响力分析完成（${mlabel}${group ? " · " + groupLabel(group) : ""}，前 ${data.results.length} 名已高亮于画布：节点越大、金色边框越醒目表示影响力越高）`);
  } catch (e) {
    setStatus("影响力分析失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "运行影响力分析"; }
  }
}

function renderInfluence(data) {
  const box = $("#inf-list");
  box.innerHTML = "";
  const results = data.results || [];
  if (!results.length) { box.innerHTML = `<div class="hint">无结果。</div>`; return; }
  const max = Math.max(...results.map((r) => r.score || 0)) || 1;
  results.forEach((r, i) => {
    const gc = (GROUP_COLOR[r.group] && GROUP_COLOR[r.group].color.background) || "#888";
    const pct = Math.max(2, Math.round(((r.score || 0) / max) * 100));
    const item = document.createElement("div");
    item.className = "inf-item";
    item.innerHTML =
      `<span class="inf-rank">${i + 1}</span>` +
      `<div class="inf-main">` +
        `<div class="inf-top"><span class="inf-name">${esc(r.label)}</span>` +
        `<span class="inf-grp" style="background:${gc}">${esc(groupLabel(r.group))}</span>` +
        `<span class="inf-score">${Number(r.score).toFixed(4)}</span></div>` +
        `<div class="inf-bar"><span style="width:${pct}%;background:${gc}"></span></div>` +
      `</div>`;
    item.addEventListener("click", () => focusOrLoadNode(r.id));
    box.appendChild(item);
  });
}

// 把中心性分数映射成节点尺寸（sqrt 让差异更可读）：score=0 → 12，score=max → 46。
function sizeForScore(score, max) {
  const t = max > 0 ? Math.sqrt(Math.max(0, score) / max) : 0;
  return 12 + 34 * t;
}

// 影响力分析的可视化落地：把前 N 名节点直接“钉”在画布上并强调，让分析有可见效果。
// 已在画布的节点就地放大 + 加金色描边；不在画布的（如起始为空）则补加为孤立高亮节点，
// 保证无论当前画布状态，点击“运行影响力分析”都有明确反馈。
function applyInfluenceHighlight(data) {
  clearGraphHighlights();
  const results = data.results || [];
  if (!results.length) return;
  const max = Math.max(...results.map((r) => r.score || 0)) || 1;
  const topIds = [];
  for (const r of results) {
    const size = sizeForScore(r.score, max);
    const existing = nodes.get(r.id);
    if (existing) {
      nodes.update({
        id: r.id,
        size,
        borderWidth: 3,
        color: Object.assign({}, existing.color, { border: "#f5b301" }),
      });
    } else {
      const g = (GROUP_COLOR[r.group] && GROUP_COLOR[r.group].color) || { background: "#888", border: "#555" };
      nodes.add({
        id: r.id,
        label: r.label || r.id,
        group: r.group,
        title: `${r.label || r.id}\n${groupLabel(r.group)}（影响力 ${Number(r.score).toFixed(4)}）`,
        color: { background: g.background, border: "#f5b301" },
        borderWidth: 3,
        size,
      });
    }
    topIds.push(r.id);
    highlightedNodeIds.add(r.id);
  }
  if (network) network.fit({ nodes: topIds, animation: true });
}

// 影响力榜点击：节点已在画布就聚焦；否则拉取其 1 跳邻居后再聚焦（不清除现有视图）。
async function focusOrLoadNode(id) {
  if (nodes.get(id)) {
    if (network) { network.selectNodes([id]); network.focus(id, { animation: true, scale: 1.1 }); }
    setStatus(`已聚焦：${displayLabel(nodes.get(id))}`);
    return;
  }
  setStatus("加载节点…");
  try {
    await expandFrom(id, 1, collectTypes(), true);
    if (network) { network.selectNodes([id]); network.focus(id, { animation: true, scale: 1.1 }); }
    const n = nodes.get(id);
    setStatus(`已加载并聚焦：${n ? displayLabel(n) : id}`);
  } catch (e) {
    setStatus("聚焦节点失败：" + e.message);
  }
}

/* ---------------- 表格浏览 ---------------- */
let tblOffset = 0;
const TBL_LIMIT = 50;

async function loadTable(reset) {
  if (reset) { tblOffset = 0; $("#tbl-body").innerHTML = ""; }
  const group = $("#tbl-group").value;
  const q = $("#tbl-q").value.trim();
  const offset = tblOffset;
  setStatus("加载表格…");
  try {
    const params = new URLSearchParams({ limit: String(TBL_LIMIT), offset: String(offset) });
    if (group && group !== "all") params.set("group", group);
    if (q) params.set("q", q);
    const data = await fetchJSON(`${API}/graph/list?${params}`);
    const body = $("#tbl-body");
    if (!data.items || !data.items.length) {
      $("#tbl-meta").textContent = reset ? "无结果" : "没有更多了";
      $("#tbl-more").style.display = "none";
      setStatus("");
      return;
    }
    for (const n of data.items) {
      const tr = document.createElement("tr");
      tr.innerHTML =
        `<td>${esc(displayLabel(n))}</td><td>${esc(n.group)}</td><td>${esc(n.id)}</td>`;
      tr.addEventListener("click", () => {
        selectNode(n.id);
        if (network) network.focus(n.id, { animation: true, scale: 1.1 });
        setStatus(`已从表格进入「${displayLabel(n)}」的探索（已展开 1 跳）`);
      });
      body.appendChild(tr);
    }
    tblOffset = offset + data.items.length;
    const shown = tblOffset;
    $("#tbl-meta").textContent = `共 ${data.total} 个，已显示 ${shown}`;
    $("#tbl-more").style.display = shown < data.total ? "block" : "none";
    setStatus("");
  } catch (e) {
    setStatus("表格加载失败：" + e.message);
  }
}

/* ---------------- 清空画布 ---------------- */
function clearCanvas() {
  nodes.clear();
  edges.clear();
  if (network) network.unselectAll();
  current = pathA = pathB = null;
  $("#path-a").textContent = "未选"; $("#path-b").textContent = "未选";
  const ia = $("#path-a-input"), ib = $("#path-b-input");
  if (ia) ia.value = ""; if (ib) ib.value = "";
  renderPathResult(null, 0);
  mode = "explore";
  const cl = $("#comm-list");
  if (cl) cl.innerHTML = "";
  setStatus("画布已清空。可重新渲染种子，或从上方搜索 / 表格开始探索。");
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $("#search-btn").addEventListener("click", doSearch);
  $("#search-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  $("#hops").addEventListener("input", (e) => { $("#hops-val").textContent = e.target.value; });
  $("#seed-btn").addEventListener("click", renderSeedFilter);
  $("#seed-hops").addEventListener("input", (e) => { $("#seed-hops-val").textContent = e.target.value; });
  $("#seed-tags-clear").addEventListener("click", () => {
    const sel = $("#seed-tags");
    for (const o of sel.options) o.selected = false;
    setStatus("已清空标签选择");
  });
  $("#tbl-btn").addEventListener("click", () => loadTable(true));
  $("#tbl-group").addEventListener("change", () => loadTable(true));
  $("#tbl-q").addEventListener("keydown", (e) => { if (e.key === "Enter") loadTable(true); });
  $("#tbl-more").addEventListener("click", () => loadTable(false));
  $("#reset-btn").addEventListener("click", clearCanvas);
  $("#clear-btn").addEventListener("click", clearCanvas);
  $("#fit-btn").addEventListener("click", focusGraph);
  $("#comm-btn").addEventListener("click", loadCommunities);
  $("#comm-algo").addEventListener("change", () => { stopCommunityAnimation(); renderCommParams(); });
  $("#comm-animate").addEventListener("change", () => { /* 仅运行前读取，无需即时响应 */ });
  $("#comm-anim-play").addEventListener("click", commAnimPlayPause);
  $("#comm-anim-speed").addEventListener("change", () => { if (commAnim.playing) scheduleAnimTick(); });
  $("#comm-anim-seek").addEventListener("input", (e) => commAnimSeek(parseInt(e.target.value, 10) || 0));
  $("#inf-btn").addEventListener("click", loadInfluence);
  $("#inf-metric").addEventListener("change", updateInfMetricNote);
  updateInfMetricNote();
  loadCommunityAlgorithms(); // 进入即从 /communities/meta 拉取算法目录并渲染下拉 + 参数表单
  // 社团成员侧栏面板关闭
  $("#comm-panel-close").addEventListener("click", closeCommPanel);
  // 最短路径节点选择器（带搜索联想）
  makePicker("path-a-input", "path-a-results", "a");
  makePicker("path-b-input", "path-b-results", "b");
  $("#path-swap").addEventListener("click", swapPath);
  // 点击画布外的任意处收起搜索下拉
  document.addEventListener("click", (e) => {
    const wrap = document.querySelector(".search-wrap");
    if (wrap && !wrap.contains(e.target)) hideSearch();
  });
  $("#expand-btn").addEventListener("click", () => {
    expandFrom(current, parseInt($("#hops").value, 10), collectTypes(), true);
  });
  $("#set-a-btn").addEventListener("click", () => {
    if (current) { const n = nodes.get(current) || { id: current }; setPathNode("a", n); setStatus(`已将当前节点设为起点：${displayLabel(n)}`); }
    else setStatus("请先点选一个节点作为起点");
  });
  $("#set-b-btn").addEventListener("click", () => {
    if (current) { const n = nodes.get(current) || { id: current }; setPathNode("b", n); setStatus(`已将当前节点设为终点：${displayLabel(n)}`); }
    else setStatus("请先点选一个节点作为终点");
  });
  $("#path-btn").addEventListener("click", computePath);
  $("#path-clear").addEventListener("click", () => {
    pathA = pathB = null;
    $("#path-a").textContent = "未选"; $("#path-b").textContent = "未选";
    const ia = $("#path-a-input"), ib = $("#path-b-input");
    if (ia) ia.value = ""; if (ib) ib.value = "";
    renderPathResult(null, 0);
    nodes.clear(); edges.clear(); mode = "explore"; setStatus("已清除路径");
  });
  $("#reset-btn").addEventListener("click", () => {
    nodes.clear(); edges.clear(); current = pathA = pathB = null;
    $("#path-a").textContent = "未选"; $("#path-b").textContent = "未选";
    const ia = $("#path-a-input"), ib = $("#path-b-input");
    if (ia) ia.value = ""; if (ib) ib.value = "";
    renderPathResult(null, 0);
    mode = "explore"; setStatus("已重置");
  });
}

/* ---------------- 用户会话（退出登录 / 查看个人信息） ---------------- */
let currentUser = null;

// 拉取当前登录用户；未登录（理论上守卫已跳 /login，这里兜底隐藏用户区）。
async function loadUser() {
  const ua = document.getElementById("user-area");
  // 免登录调试模式（GOTY_AUTH_ENABLED=false）：账号体系整体关闭，
  // 直接隐藏用户区即可，无需再请求 /api/auth/me —— 否则控制台会多一条 401 噪音。
  if (backendMeta && backendMeta.auth_enabled === false) {
    if (ua) ua.hidden = true;
    return;
  }
  try {
    const r = await fetch(`${API}/auth/me`, { credentials: "same-origin" });
    if (!r.ok) { if (ua) ua.hidden = true; return; }
    const u = await r.json();
    currentUser = u;
    if (ua) ua.hidden = false;
    const greet = document.getElementById("user-greeting");
    if (greet) greet.textContent = "你好，" + (u.username || "");
    fillProfile(u);
  } catch (e) {
    // 原注释假设 probeBackend 会兜底，但两者是两次独立请求：它能成功不代表
    // /auth/me 可达。此处静默会让用户区无声消失且无任何提示。
    if (ua) ua.hidden = true;
    setStatus("登录状态获取失败：" + e.message);
  }
}

function fillProfile(u) {
  const pu = document.getElementById("pf-username");
  const pe = document.getElementById("pf-email");
  if (pu) pu.textContent = u && u.username ? u.username : "—";
  if (pe) pe.textContent = u && u.email ? u.email : "（未填写）";
}

function bindUser() {
  const logout = document.getElementById("logout-btn");
  if (logout) logout.addEventListener("click", async () => {
    let ok = false;
    try {
      const r = await fetch(`${API}/auth/logout`, { method: "POST", credentials: "same-origin" });
      ok = r.ok;
    } catch (e) {
      ok = false;
    }
    if (!ok) {
      // 不能照样跳转：服务端会话未销毁时守卫会把用户立刻弹回 /explore，
      // 表现为「点了退出没反应」。
      setStatus("退出登录失败，请重试");
      return;
    }
    // 退出后回登录页（守卫会把未登录的 /explore 跳回 /login?next=/explore/）
    location.href = "/login?next=/explore/";
  });
  const prof = document.getElementById("profile-btn");
  if (prof) prof.addEventListener("click", (e) => {
    e.stopPropagation();
    const p = document.getElementById("profile-panel");
    if (p) p.hidden = !p.hidden;
  });
  // 点页面其它区域时关闭个人信息浮层
  document.addEventListener("click", (e) => {
    const p = document.getElementById("profile-panel");
    const btn = document.getElementById("profile-btn");
    if (p && !p.hidden && btn && !btn.contains(e.target) && !p.contains(e.target)) p.hidden = true;
  });
}

/* ---------------- 启动 ---------------- */
// 全局错误兜底：任何未捕获异常都显式暴露，避免「静默失败 → 误以为功能无效」。
window.addEventListener("error", (ev) => {
  const msg = (ev && ev.message) || (ev && ev.error && ev.error.message) || "未知脚本错误";
  setStatus("脚本错误：" + msg);
  showConnBanner("⚠ 前端脚本出错：「" + esc(msg) + "」。请打开浏览器控制台（F12）查看详情，或硬刷新（Ctrl/Cmd+Shift+R）绕过缓存。");
});
window.addEventListener("unhandledrejection", (ev) => {
  const r = ev && ev.reason;
  setStatus("未处理异常：" + (r && r.message ? r.message : String(r)));
});

initNetwork();
bind();
bindUser();       // 绑定退出登录 / 个人信息
// 先探测后端以拿到 meta（含 auth_enabled），再决定是否拉取登录用户，
// 这样免登录模式下不会发出无谓的 /api/auth/me 请求。
probeBackend().then(loadUser); // 连接探测 + 登录态（免登录模式自动跳过）

loadTagOptions();  // 填充「渲染种子」的标签多选框（类型节点）
loadTable(true);   // 进入即填充表格，方便从任意节点开始探索
loadDefaultSeed(); // 进入即展示 GOTY 获奖作品的初始子图，避免空白画布
