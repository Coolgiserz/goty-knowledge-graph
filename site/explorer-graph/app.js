// GOTY 知识图谱 · 交互式探索浏览器（独立前端，不依赖原 site/explorer）
// 仅调用后端既有只读端点 /api/graph/{search,node,traverse,path}；
// 这些端点在 GOTY_GRAPH_BACKEND=neo4j 时由 Cypher 驱动（用户无感，仅状态栏可见后端标识）。

const API = `http://${location.hostname}:8080/api`;

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
  const r = await fetch(url);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try { const j = await r.json(); if (j && j.detail) msg = j.detail; } catch (_) {}
    throw new Error(msg);
  }
  return r.json();
}
function displayLabel(n) { return n.label || n.id; }
function setStatus(t) { $("#status").textContent = t || ""; }
function setBackend(b) {
  const el = $("#backend-badge");
  if (!b) { el.textContent = "后端：—"; el.className = "badge nx"; return; }
  el.textContent = "后端：" + b;
  el.className = "badge " + (b === "neo4j" ? "neo4j" : "nx");
}

/* ---------------- vis-network ---------------- */
function initNetwork() {
  const container = $("#canvas");
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
    interaction: { hover: true, tooltipDelay: 120 },
  });
  network.on("click", (params) => {
    if (params.nodes && params.nodes.length) selectNode(params.nodes[0]);
  });
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
  edges.add({
    id: k,
    from: e.source,
    to: e.target,
    label: REL_LABEL[e.type] || e.type,
    title: e.type,
  });
}
function loadSubgraph(sub) {
  if (!sub || !sub.center) return false;
  upsertNode(sub.center);
  (sub.nodes || []).forEach(upsertNode);
  (sub.edges || []).forEach(upsertEdge);
  return true;
}

/* ---------------- 搜索 ---------------- */
async function doSearch() {
  const q = $("#search-input").value.trim();
  if (!q) return;
  setStatus("搜索中…");
  try {
    const data = await fetchJSON(`${API}/graph/search?q=${encodeURIComponent(q)}&limit=20`);
    if (data.backend) setBackend(data.backend);
    const box = $("#search-results");
    box.innerHTML = "";
    if (!data.results || !data.results.length) {
      box.innerHTML = `<div class="hint">无匹配结果。</div>`;
      setStatus("");
      return;
    }
    for (const n of data.results) {
      const item = document.createElement("div");
      item.className = "result-item";
      item.innerHTML =
        `<div class="ri-label">${esc(displayLabel(n))}</div>` +
        `<div class="ri-group">${esc(n.group)} · ${esc(n.id)}</div>`;
      item.addEventListener("click", () => selectNode(n.id));
      box.appendChild(item);
    }
    setStatus(`找到 ${data.results.length} 个匹配`);
  } catch (e) {
    setStatus("搜索失败：" + e.message);
  }
}

/* ---------------- 节点详情 + 自动展开 ---------------- */
async function selectNode(id) {
  current = id;
  updatePathPickLabels();
  setStatus("加载节点…");
  try {
    const n = await fetchJSON(`${API}/graph/node/${encodeURIComponent(id)}`);
    renderDetail(n);
  } catch (e) {
    setStatus("节点详情失败：" + e.message);
  }
  // 选中即展开 1 跳，让画布有内容
  await expandFrom(id, 1, collectTypes(), true);
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
    `<div class="d-group">${esc(n.group)} · ${esc(n.id)}</div>` +
    (rows ? `<dl>${rows}</dl>` : `<div class="hint">无更多属性</div>`);
}

function collectTypes() {
  return [...document.querySelectorAll(".rel-type:checked")].map((c) => c.value);
}

async function expandFrom(id, hops, types, merge) {
  if (!id) { setStatus("请先选择一个节点"); return; }
  if (merge) { mode = "explore"; }
  setStatus("展开邻居…");
  try {
    const params = new URLSearchParams({ start: id, hops: String(hops) });
    if (types && types.length) params.set("types", types.join(","));
    const data = await fetchJSON(`${API}/graph/traverse?${params}`);
    if (data.backend) setBackend(data.backend);
    if (!loadSubgraph(data)) {
      setStatus("节点未找到");
      return;
    }
    setStatus(`已展开（${data.nodes ? data.nodes.length : 0} 个邻居，后端：${data.backend || "?"}）`);
  } catch (e) {
    setStatus("展开失败：" + e.message);
  }
}

/* ---------------- 最短路径 ---------------- */
function updatePathPickLabels() {
  $("#path-a").textContent = pathA ? pathA : "未选";
  $("#path-b").textContent = pathB ? pathB : "未选";
}
async function computePath() {
  if (!pathA || !pathB) { setStatus("请先设置起点与终点"); return; }
  if (pathA === pathB) { setStatus("起点与终点相同"); return; }
  setStatus("计算最短路径…");
  try {
    const data = await fetchJSON(
      `${API}/graph/path?a=${encodeURIComponent(pathA)}&b=${encodeURIComponent(pathB)}`
    );
    if (data.backend) setBackend(data.backend);
    if (!data.nodes || !data.nodes.length) {
      setStatus("两节点之间无路径");
      return;
    }
    // 路径模式：清空画布，仅展示路径
    mode = "path";
    nodes.clear();
    edges.clear();
    data.nodes.forEach(upsertNode);
    data.edges.forEach(upsertEdge);
    setStatus(`最短路径长度 ${data.length}（后端：${data.backend || "?"}）`);
  } catch (e) {
    setStatus("路径计算失败：" + e.message);
  }
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  $("#search-btn").addEventListener("click", doSearch);
  $("#search-input").addEventListener("keydown", (e) => { if (e.key === "Enter") doSearch(); });
  $("#hops").addEventListener("input", (e) => { $("#hops-val").textContent = e.target.value; });
  $("#expand-btn").addEventListener("click", () => {
    expandFrom(current, parseInt($("#hops").value, 10), collectTypes(), true);
  });
  $("#set-a-btn").addEventListener("click", () => { if (current) { pathA = current; updatePathPickLabels(); } });
  $("#set-b-btn").addEventListener("click", () => { if (current) { pathB = current; updatePathPickLabels(); } });
  $("#path-btn").addEventListener("click", computePath);
  $("#path-clear").addEventListener("click", () => {
    pathA = pathB = null; updatePathPickLabels();
    nodes.clear(); edges.clear(); mode = "explore"; setStatus("已清除路径");
  });
  $("#reset-btn").addEventListener("click", () => {
    nodes.clear(); edges.clear(); current = pathA = pathB = null;
    updatePathPickLabels(); mode = "explore"; setStatus("已重置");
  });
}

/* ---------------- 启动 ---------------- */
initNetwork();
bind();
setStatus("就绪：搜索一个节点开始探索。开启 Neo4j 后端后，左下角“后端”会变为 neo4j。");
