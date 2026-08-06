// GOTY 知识图谱 · 数据探索 SPA（原生 ES Module，无构建步骤）
// 负责：板块导航 / 参数面板（依据 schema 自动生成）/ 异步任务提交与轮询 /
//       渲染可视化（network/heatmap/scatter/bar，纯 SVG）/ 双有效性解读框 /
//       后台任务管理（状态 / 结果 / 取消）/ 访问令牌。

const API_BASE = window.API_BASE || "";
const API = `${API_BASE}/api`;
const TOKEN_KEY = "goty_explore_token";

const PALETTE = ["#f5b301", "#3b6ea5", "#27ae60", "#8e44ad", "#e74c3c",
  "#16a085", "#d35400", "#7f8c8d", "#2980b9", "#c0392b", "#2ecc71",
  "#9b59b6", "#e67e22", "#1abc9c", "#ecf0f1"];

const tooltip = document.getElementById("tooltip");

/* ---------------- 小工具 ---------------- */
const $ = (sel, root = document) => root.querySelector(sel);
function el(tag, attrs = {}, children = []) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else if (v !== null && v !== undefined) e.setAttribute(k, v);
  }
  for (const c of [].concat(children)) {
    if (c == null) continue;
    e.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return e;
}
const SVGNS = "http://www.w3.org/2000/svg";
function svg(tag, attrs = {}) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
async function fetchJSON(url, opts = {}) {
  const r = await fetch(url, opts);
  if (!r.ok) {
    let detail = `${r.status} ${r.statusText}`;
    let retryAfter = null;
    try {
      const j = await r.json();
      if (j && j.message) detail = j.message;
      if (j && j.retry_after) retryAfter = j.retry_after;
    } catch (_) { /* 非 JSON 错误体，保留默认描述 */ }
    const err = new Error(detail);
    err.status = r.status;
    err.retryAfter = retryAfter;
    throw err;
  }
  return r.json();
}
function friendlyError(e) {
  if (e && e.status === 429) {
    const sec = e.retryAfter ? ` 约 ${e.retryAfter}s 后可重试` : "";
    return `请求过于频繁，已被限流。${sec}`;
  }
  if (e && e.status === 403) {
    if (/exploration_disabled/.test(e.message || ""))
      return "探索模式未开放：当前为只读浏览（仅图谱/表格）。";
    return `访问受限：您的地址已被加入黑名单，请联系管理员。`;
  }
  if (e && e.status === 401) return "需要有效的访问令牌才能提交探索任务（在左侧粘贴令牌）。";
  return `请求失败：${e && e.message ? e.message : "未知错误"}`;
}
function showTip(html, ev) {
  tooltip.innerHTML = html;
  tooltip.hidden = false;
  tooltip.style.left = (ev.clientX + 14) + "px";
  tooltip.style.top = (ev.clientY + 14) + "px";
}
function hideTip() { tooltip.hidden = true; }
function lerpColor(a, b, t) {
  const pa = hex2rgb(a), pb = hex2rgb(b);
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
function hex2rgb(h) {
  h = h.replace("#", "");
  if (h.length === 3) h = h.split("").map((x) => x + x).join("");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/* ---------------- 访问令牌 ---------------- */
function getToken() {
  const v = ($("#explore-token") && $("#explore-token").value) || "";
  return v.trim();
}
function initToken() {
  const box = $("#explore-token");
  if (!box) return;
  const saved = localStorage.getItem(TOKEN_KEY) || "";
  box.value = saved;
  box.addEventListener("input", () => {
    localStorage.setItem(TOKEN_KEY, box.value.trim());
  });
}
function authHeaders(extra = {}) {
  const h = { "Content-Type": "application/json", ...extra };
  const t = getToken();
  if (t) h["Authorization"] = `Bearer ${t}`;
  return h;
}

/* ---------------- 状态 ---------------- */
let BOARDS = [];
let CURRENT = null;
let busy = false;
let pollTimer = null;
let currentJobId = null;

/* ---------------- 初始化 ---------------- */
async function init() {
  initToken();
  bindRefresh();
  try {
    const meta = await fetchJSON(`${API}/meta`);
    renderDataStatus(meta);
    if (meta.exploration_enabled === false) {
      showExplorationDisabled();
      return;
    }
    const data = await fetchJSON(`${API}/boards`);
    BOARDS = data.boards || [];
  } catch (e) {
    $("#result").innerHTML = `<div class="err-msg">无法连接 API（${esc(friendlyError(e))}）。请确认后端已启动。</div>`;
    return;
  }
  renderNav();
  if (BOARDS.length) selectBoard(BOARDS[0].name);
}

function showExplorationDisabled() {
  // 云端默认：只读浏览，隐藏探索控件，提示去看原图谱/表格
  const aux = $(".side-aux");
  if (aux) aux.style.display = "none";
  const params = $("#params");
  if (params) params.style.display = "none";
  $("#result").innerHTML =
    `<div class="info-msg">探索模式未开放（当前为只读浏览）。<br>` +
    `请在右上角「原图谱浏览」查看交互式图谱与表格，或联系管理员开放数据挖掘模式。</div>`;
}

function renderDataStatus(meta) {
  const box = $("#data-status");
  const ok = meta.data_matches_baseline;
  if (ok === true) {
    box.className = "data-status ok";
    box.textContent = `数据一致 ✓（${meta.sha256}）`;
  } else if (ok === false) {
    box.className = "data-status drift";
    box.textContent = `数据漂移 ⚠（${meta.sha256}）`;
  } else {
    box.className = "data-status loading";
    box.textContent = "数据基线缺失";
  }
}

function renderNav() {
  const nav = $("#board-nav");
  nav.innerHTML = "";
  for (const b of BOARDS) {
    const item = el("button", {
      class: "nav-item",
      "data-name": b.name,
      onclick: () => selectBoard(b.name),
    }, [
      el("span", { class: "ni-label" }, b.label),
      el("span", { class: "ni-desc" }, b.description),
    ]);
    nav.appendChild(item);
  }
}

function selectBoard(name) {
  CURRENT = BOARDS.find((b) => b.name === name);
  if (!CURRENT) return;
  document.querySelectorAll(".nav-item").forEach((n) =>
    n.classList.toggle("active", n.getAttribute("data-name") === name));
  renderBoardHeader(CURRENT);
  renderParams(CURRENT);
  submitJob(); // 选中板块即提交一次默认参数的后台任务
}

function renderBoardHeader(b) {
  $("#board-header").innerHTML = "";
  $("#board-header").appendChild(el("h2", {}, b.label));
  $("#board-header").appendChild(el("p", {}, b.description));
}

/* ---------------- 参数面板 ---------------- */
function defaultParams(b) {
  const o = {};
  for (const p of b.params) o[p.key] = p.default;
  return o;
}
function renderParams(b) {
  const panel = $("#params");
  panel.innerHTML = "";
  panel.appendChild(el("p", { class: "pp-title" }, "参数（调节后点「应用参数」提交后台任务，并判定解读有效性）"));

  const groups = {};
  for (const p of b.params) {
    const g = p.group || "常规";
    (groups[g] = groups[g] || []).push(p);
  }
  for (const [gname, ps] of Object.entries(groups)) {
    const g = el("div", { class: "param-group" });
    if (gname !== "常规") g.appendChild(el("div", { class: "pg-name" }, gname));
    for (const p of ps) g.appendChild(renderParamControl(p));
    panel.appendChild(g);
  }
  const btn = el("button", { class: "apply-btn", onclick: submitJob }, "应用参数");
  panel.appendChild(el("div", { style: "margin-top:14px" }, [btn]));
}

function renderParamControl(p) {
  const row = el("div", { class: "param-row", "data-key": p.key });
  row.appendChild(el("label", { for: `p-${p.key}` }, p.label));
  const ctl = el("div", { class: "ctl" });

  if (p.type === "select") {
    const sel = el("select", { id: `p-${p.key}` });
    for (const o of (p.options || [])) {
      const opt = el("option", { value: o }, o);
      if (o === p.default) opt.setAttribute("selected", "selected");
      sel.appendChild(opt);
    }
    ctl.appendChild(sel);
  } else if (p.type === "bool") {
    const cb = el("input", { id: `p-${p.key}`, type: "checkbox" });
    if (p.default) cb.setAttribute("checked", "checked");
    ctl.appendChild(cb);
  } else { // int / float → 滑块
    const hasRange = typeof p.min === "number" && typeof p.max === "number";
    if (hasRange) {
      const val = el("span", { class: "val" }, String(p.default));
      const range = el("input", {
        id: `p-${p.key}`, type: "range",
        min: p.min, max: p.max, step: p.step || 1, value: p.default,
        oninput: (e) => { val.textContent = e.target.value; },
      });
      ctl.appendChild(range); ctl.appendChild(val);
    } else {
      ctl.appendChild(el("input", { id: `p-${p.key}`, type: "number", value: p.default }));
    }
  }
  row.appendChild(ctl);
  if (p.help) row.appendChild(el("div", { class: "help" }, p.help));
  return row;
}

function collectParams() {
  const out = {};
  for (const p of CURRENT.params) {
    const node = document.getElementById(`p-${p.key}`);
    if (!node) { out[p.key] = p.default; continue; }
    if (p.type === "bool") out[p.key] = node.checked;
    else if (p.type === "int") out[p.key] = parseInt(node.value, 10);
    else if (p.type === "float") out[p.key] = parseFloat(node.value);
    else out[p.key] = node.value;
  }
  return out;
}

/* ---------------- 异步任务提交 + 轮询 ---------------- */
async function submitJob() {
  if (!CURRENT || busy) return;
  busy = true;
  const params = collectParams();
  const resultBox = $("#result");
  resultBox.innerHTML = `<div class="loading-msg">已提交后台任务，计算中（不阻塞页面）…</div>`;
  try {
    const job = await fetchJSON(`${API}/jobs`, {
      method: "POST",
      headers: authHeaders(),
      body: JSON.stringify({ board: CURRENT.name, params }),
    });
    currentJobId = job.id;
    loadJobs();
    pollJob(job.id);
  } catch (e) {
    resultBox.innerHTML = `<div class="err-msg">${esc(friendlyError(e))}</div>`;
  } finally {
    busy = false;
  }
}

function pollJob(id) {
  if (pollTimer) clearTimeout(pollTimer);
  const tick = async () => {
    try {
      const job = await fetchJSON(`${API}/jobs/${id}`, { headers: authHeaders() });
      const box = $("#result");
      if (job.status === "pending") {
        box.innerHTML = `<div class="loading-msg">排队中…（任务 ${id}）</div>`;
        pollTimer = setTimeout(tick, 1000);
      } else if (job.status === "running") {
        box.innerHTML = `<div class="loading-msg">计算中…（任务 ${id}）</div>`;
        pollTimer = setTimeout(tick, 1000);
      } else if (job.status === "done") {
        renderResult(job.result);
        loadJobs();
      } else if (job.status === "failed") {
        box.innerHTML = `<div class="err-msg">任务失败：${esc(job.error || "未知错误")}</div>`;
        loadJobs();
      } else if (job.status === "canceled") {
        box.innerHTML = `<div class="info-msg">任务已取消（${id}）。</div>`;
        loadJobs();
      }
    } catch (e) {
      $("#result").innerHTML = `<div class="err-msg">${esc(friendlyError(e))}</div>`;
    }
  };
  tick();
}

/* ---------------- 后台任务管理 UI ---------------- */
function bindRefresh() {
  const btn = $("#refresh-jobs");
  if (btn) btn.addEventListener("click", loadJobs);
}
async function loadJobs() {
  const list = $("#tasks-list");
  if (!list) return;
  try {
    const url = getToken() ? `${API}/jobs?scope=all` : `${API}/jobs`;
    const data = await fetchJSON(url, { headers: authHeaders() });
    const jobs = data.jobs || [];
    if (!jobs.length) {
      list.innerHTML = `<p class="hint">暂无任务。调节参数后点「应用参数」提交。</p>`;
      return;
    }
    list.innerHTML = "";
    for (const j of jobs) list.appendChild(renderJobRow(j));
  } catch (e) {
    list.innerHTML = `<p class="err-msg">${esc(friendlyError(e))}</p>`;
  }
}
function statusBadge(s) {
  const map = {
    pending: ["badge pending", "排队中"],
    running: ["badge running", "计算中"],
    done: ["badge done", "完成"],
    failed: ["badge failed", "失败"],
    canceled: ["badge canceled", "已取消"],
  };
  const [cls, label] = map[s] || ["badge", s];
  return el("span", { class: cls }, label);
}
function renderJobRow(j) {
  const row = el("div", { class: "job-row", "data-id": j.id });
  row.appendChild(statusBadge(j.status));
  row.appendChild(el("span", { class: "job-board" }, j.board));
  row.appendChild(el("span", { class: "job-id" }, `#${j.id}`));
  const actions = el("div", { class: "job-actions" });
  if (j.status === "done") {
    actions.appendChild(el("button", {
      class: "mini-btn", type: "button",
      onclick: () => showJobResult(j.id),
    }, "结果"));
  }
  if (j.status === "pending" || j.status === "running") {
    actions.appendChild(el("button", {
      class: "mini-btn danger", type: "button",
      onclick: () => cancelJob(j.id),
    }, "取消"));
  }
  row.appendChild(actions);
  return row;
}
async function showJobResult(id) {
  try {
    const job = await fetchJSON(`${API}/jobs/${id}`, { headers: authHeaders() });
    if (job.status === "done") renderResult(job.result);
    else $("#result").innerHTML = `<div class="info-msg">任务 ${id} 状态：${job.status}，暂无可渲染结果。</div>`;
  } catch (e) {
    $("#result").innerHTML = `<div class="err-msg">${esc(friendlyError(e))}</div>`;
  }
}
async function cancelJob(id) {
  try {
    await fetchJSON(`${API}/jobs/${id}`, { method: "DELETE", headers: authHeaders() });
    loadJobs();
  } catch (e) {
    $("#result").innerHTML = `<div class="err-msg">${esc(friendlyError(e))}</div>`;
  }
}

/* ---------------- 渲染结果（原样复用） ---------------- */
function renderValidityBanner(res) {
  const v = res.validity || {};
  if (v.data_matches_baseline === false) {
    return el("div", { class: "validity-banner bad" }, [
      el("strong", {}, "⚠ 数据已漂移"),
      "　底层 graph.json 与文档快照基线不一致，",
      "所有板块的「预写解读」均视为失效，请以图中实际结果为准。",
    ]);
  }
  if (v.interpretation_valid === false) {
    const reasons = (v.invalid_reasons || []).map((r) =>
      el("li", {}, `「${r.key}」偏离默认（默认 ${esc(JSON.stringify(r.expected))} → 当前 ${esc(JSON.stringify(r.actual))}）`));
    return el("div", { class: "validity-banner warn" }, [
      el("strong", {}, "⚠ 参数偏离默认口径"),
      "　你调节了会改变结论的参数，以下「解读」可能不再成立（已置灰），请以图中实际数据为准。",
      el("ul", {}, reasons),
    ]);
  }
  return null;
}

function renderResult(res) {
  const box = $("#result");
  box.innerHTML = "";

  const banner = renderValidityBanner(res);
  if (banner) box.appendChild(banner);

  if (res.metrics && Object.keys(res.metrics).length) {
    const m = el("div", { class: "metrics" });
    for (const [k, v] of Object.entries(res.metrics)) {
      let disp = v;
      if (typeof v === "object") disp = JSON.stringify(v);
      m.appendChild(el("div", { class: "metric" }, [
        el("div", { class: "m-val" }, String(disp)),
        el("div", { class: "m-key" }, k),
      ]));
    }
    box.appendChild(m);
  }

  if (res.error) box.appendChild(el("div", { class: "err-msg" }, res.error));

  for (const panel of (res.panels || [])) {
    box.appendChild(renderPanel(panel));
  }
  for (const t of (res.tables || [])) {
    box.appendChild(renderTable(t));
  }
  if (res.interpretation) box.appendChild(renderInterpretation(res));
}

function renderPanel(panel) {
  const card = el("div", { class: "panel-card" }, [el("h3", {}, panel.title || "")]);
  const wrap = el("div", { class: "chart-wrap" });
  if (panel.caption) card.appendChild(el("p", { class: "caption" }, panel.caption));
  const data = panel.data || {};
  if (panel.type === "network") wrap.appendChild(renderNetwork(data));
  else if (panel.type === "heatmap") wrap.appendChild(renderHeatmap(data));
  else if (panel.type === "scatter") wrap.appendChild(renderScatter(data));
  else if (panel.type === "bar") wrap.appendChild(renderBar(data));
  else wrap.appendChild(el("div", { class: "loading-msg" }, `未知面板类型：${panel.type}`));
  card.appendChild(wrap);
  return card;
}

/* ---------------- 网络图 ---------------- */
function renderNetwork(data) {
  const nodes = data.nodes || [];
  const edges = data.edges || [];
  const W = 820, H = 600, pad = 30;
  const xs = nodes.map((n) => n.x), ys = nodes.map((n) => n.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = (x) => pad + ((x - minX) / (maxX - minX || 1)) * (W - 2 * pad);
  const sy = (y) => pad + ((y - minY) / (maxY - minY || 1)) * (H - 2 * pad);

  const commIds = [...new Set(nodes.map((n) => n.community))].sort((a, b) => a - b);
  const commColor = {};
  commIds.forEach((c, i) => { commColor[c] = PALETTE[i % PALETTE.length]; });

  const s = svg("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, role: "img" });
  for (const e of edges) {
    const a = nodes.find((n) => n.id === e.from), b = nodes.find((n) => n.id === e.to);
    if (!a || !b) continue;
    s.appendChild(svg("line", { x1: sx(a.x), y1: sy(a.y), x2: sx(b.x), y2: sy(b.y),
      stroke: "rgba(255,255,255,0.07)", "stroke-width": 1 }));
  }
  for (const n of nodes) {
    const isG = n.goty;
    const c = svg("circle", {
      cx: sx(n.x), cy: sy(n.y), r: isG ? 7.5 : 4.5,
      fill: commColor[n.community] || "#888",
      stroke: isG ? "#fff" : "rgba(0,0,0,.4)", "stroke-width": isG ? 1.5 : 0.5,
    });
    c.addEventListener("mousemove", (ev) => showTip(
      `<b>${esc(n.label)}</b><br>社区 C${n.community}${isG ? " · GOTY ★" : ""}`, ev));
    c.addEventListener("mouseleave", hideTip);
    s.appendChild(c);
  }
  for (const n of nodes) {
    if (!n.goty) continue;
    s.appendChild(svg("text", { x: sx(n.x) + 9, y: sy(n.y) + 3,
      fill: "#e6e9ef", "font-size": 9, "pointer-events": "none" }, n.label));
  }
  const legend = el("div", { class: "legend" });
  for (const c of commIds) {
    legend.appendChild(el("span", { class: "li" }, [
      el("span", { class: "sw", style: `background:${commColor[c]}` }),
      `C${c}`,
    ]));
  }
  return el("div", {}, [s, legend]);
}

/* ---------------- 热力图 ---------------- */
function renderHeatmap(data) {
  const labels = data.labels || [];
  const matrix = data.matrix || [];
  const N = labels.length;
  const low = data.lowColor || "#1f232c";
  const high = data.highColor || "#f5b301";
  const cell = 26, labW = 84, labH = 90, pad2 = 8;
  const W = labW + N * cell + pad2;
  const H = labH + N * cell + pad2;
  const s = svg("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, role: "img" });

  for (let i = 0; i < N; i++) {
    const colLabel = svg("text", { class: "heat-axis", x: labW + i * cell + cell / 2,
      y: labH - 8, "text-anchor": "start", transform: `rotate(-55 ${labW + i * cell + cell / 2} ${labH - 8})` });
    colLabel.textContent = labels[i];
    s.appendChild(colLabel);
    const rowLabel = svg("text", { class: "heat-axis", x: labW - 6, y: labH + i * cell + cell / 2 + 3,
      "text-anchor": "end" });
    rowLabel.textContent = labels[i];
    s.appendChild(rowLabel);
  }
  for (let i = 0; i < N; i++) {
    for (let j = 0; j < N; j++) {
      const v = matrix[i] ? matrix[i][j] : 0;
      const t = Math.max(0, Math.min(1, v));
      const rect = svg("rect", {
        class: "heat-cell", x: labW + j * cell, y: labH + i * cell,
        width: cell - 1, height: cell - 1, fill: lerpColor(low, high, t),
      });
      rect.addEventListener("mousemove", (ev) => showTip(
        `<b>${esc(labels[i])}</b> × <b>${esc(labels[j])}</b><br>相似度 ${v}`, ev));
      rect.addEventListener("mouseleave", hideTip);
      s.appendChild(rect);
    }
  }
  return s;
}

/* ---------------- 散点图 ---------------- */
function renderScatter(data) {
  const series = data.series || [];
  const all = series.flatMap((s) => s.points || []);
  const W = 760, H = 520, pad = 44;
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  const minX = Math.min(...xs), maxX = Math.max(...xs);
  const minY = Math.min(...ys), maxY = Math.max(...ys);
  const sx = (x) => pad + ((x - minX) / (maxX - minX || 1)) * (W - 2 * pad);
  const sy = (y) => H - pad - ((y - minY) / (maxY - minY || 1)) * (H - 2 * pad);

  const s = svg("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, role: "img" });
  s.appendChild(svg("line", { x1: pad, y1: H - pad, x2: W - pad, y2: H - pad, stroke: "#3a4252", "stroke-width": 1 }));
  s.appendChild(svg("line", { x1: pad, y1: pad, x2: pad, y2: H - pad, stroke: "#3a4252", "stroke-width": 1 }));
  for (const sr of series) {
    for (const p of (sr.points || [])) {
      const c = svg("circle", { cx: sx(p[0]), cy: sy(p[1]), r: 5,
        fill: sr.color, "fill-opacity": 0.82, stroke: "rgba(0,0,0,.3)", "stroke-width": 0.5 });
      c.addEventListener("mousemove", (ev) => showTip(
        `<b>${esc(p[2])}</b><br>(${Number(p[0]).toFixed(2)}, ${Number(p[1]).toFixed(2)})`, ev));
      c.addEventListener("mouseleave", hideTip);
      s.appendChild(c);
    }
  }
  const legend = el("div", { class: "legend" });
  for (const sr of series) {
    legend.appendChild(el("span", { class: "li" }, [
      el("span", { class: "sw", style: `background:${sr.color}` }), sr.name,
    ]));
  }
  return el("div", {}, [s, legend]);
}

/* ---------------- 柱状图 ---------------- */
function renderBar(data) {
  const cats = data.categories || [];
  const series = data.series || [];
  const horizontal = !!data.horizontal;
  const W = 820, pad = 40;
  const sMax = Math.max(1, ...series.flatMap((s) => s.values || []));
  const nCat = cats.length, nSer = series.length;

  if (horizontal) {
    const rowH = 30, H = pad + nCat * rowH + 10;
    const s = svg("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, role: "img" });
    const plotW = W - pad - 70;
    cats.forEach((cat, i) => {
      const y = pad + i * rowH;
      const lab = svg("text", { x: 4, y: y + rowH / 2 + 3, fill: "#c8cfe0", "font-size": 11 });
      lab.textContent = cat; s.appendChild(lab);
      series.forEach((sr, si) => {
        const v = sr.values[i] || 0;
        const bw = (plotW / nSer) - 3;
        const x = pad + si * (plotW / nSer);
        const h = (v / sMax) * (rowH - 8);
        const rect = svg("rect", { x, y: y + (rowH - 6 - h), width: Math.max(0, bw), height: Math.max(0, h),
          fill: sr.color, rx: 2 });
        rect.addEventListener("mousemove", (ev) => showTip(`${esc(cat)} · ${esc(sr.name)}<br>${v}`, ev));
        rect.addEventListener("mouseleave", hideTip);
        s.appendChild(rect);
      });
    });
    appendBarLegend(s, series, W, 16);
    return s;
  } else {
    const colW = Math.min(60, (W - 2 * pad) / nCat);
    const gap = 14;
    const H = 460;
    const s = svg("svg", { class: "chart-svg", viewBox: `0 0 ${W} ${H}`, role: "img" });
    s.appendChild(svg("line", { x1: pad, y1: H - pad, x2: W - pad, y2: H - pad, stroke: "#3a4252" }));
    cats.forEach((cat, i) => {
      const grpW = colW * nSer + (nSer - 1) * 4;
      const x0 = pad + i * (colW + gap) + ((colW + gap) - grpW) / 2;
      series.forEach((sr, si) => {
        const v = sr.values[i] || 0;
        const h = (v / sMax) * (H - 2 * pad);
        const x = x0 + si * colW;
        const rect = svg("rect", { x, y: H - pad - h, width: colW - 2, height: Math.max(0, h),
          fill: sr.color, rx: 2 });
        rect.addEventListener("mousemove", (ev) => showTip(`${esc(cat)} · ${esc(sr.name)}<br>${v}`, ev));
        rect.addEventListener("mouseleave", hideTip);
        s.appendChild(rect);
      });
      const lab = svg("text", { x: x0 + grpW / 2, y: H - pad + 14, "text-anchor": "middle",
        fill: "#c8cfe0", "font-size": 10 });
      lab.textContent = cat; s.appendChild(lab);
    });
    appendBarLegend(s, series, W, 16);
    return s;
  }
}
function appendBarLegend(s, series, W, y) {
  let x = 10;
  for (const sr of series) {
    s.appendChild(svg("rect", { x, y: y - 8, width: 10, height: 10, fill: sr.color, rx: 2 }));
    const t = svg("text", { x: x + 14, y: y + 1, fill: "#8b93a7", "font-size": 10 });
    t.textContent = sr.name; s.appendChild(t);
    x += 14 + sr.name.length * 7 + 14;
  }
}

/* ---------------- 表格 ---------------- */
function renderTable(t) {
  const card = el("div", { class: "table-card" }, [el("h3", {}, t.title || "")]);
  const scroll = el("div", { class: "tbl-scroll" });
  const table = el("table", { class: "data" });
  const thead = el("thead");
  const htr = el("tr");
  for (const c of (t.columns || [])) htr.appendChild(el("th", {}, c));
  thead.appendChild(htr); table.appendChild(thead);
  const tbody = el("tbody");
  for (const row of (t.rows || [])) {
    const tr = el("tr");
    for (const cell of row) tr.appendChild(el("td", {}, String(cell)));
    tbody.appendChild(tr);
  }
  table.appendChild(tbody); scroll.appendChild(table); card.appendChild(scroll);
  return card;
}

/* ---------------- 解读框 + 双有效性 ---------------- */
function renderMarkdown(text) {
  const lines = (text || "").split("\n");
  let html = "";
  for (let line of lines) {
    if (line.trim() === "") { html += "<br>"; continue; }
    line = esc(line);
    line = line.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    if (line.startsWith("&gt; ")) {
      html += `<blockquote>${line.slice(5)}</blockquote>`;
    } else if (line.startsWith("# ")) {
      html += `<h4>${line.slice(2)}</h4>`;
    } else {
      html += `<p>${line}</p>`;
    }
  }
  return html;
}
function renderInterpretation(res) {
  const v = res.validity || {};
  const dataDrift = v.data_matches_baseline === false;
  const interpValid = v.interpretation_valid !== false;
  const invalid = dataDrift || !interpValid;

  const card = el("div", { class: `interp-card ${invalid ? "invalid" : "valid"}` });
  card.appendChild(el("h3", {}, "数据解读（默认口径下撰写）"));
  const body = el("div", { class: "interp-body", html: renderMarkdown(res.interpretation) });
  card.appendChild(body);

  if (dataDrift) {
    card.appendChild(el("span", { class: "stamp" }, "✗ 数据已漂移 · 解读失效"));
  } else if (!interpValid) {
    card.appendChild(el("span", { class: "stamp" }, "✗ 参数偏离默认 · 解读可能不成立"));
  } else {
    card.appendChild(el("span", { class: "stamp" }, "✓ 参数与默认口径一致 · 解读有效"));
  }
  return card;
}

/* ---------------- 启动 ---------------- */
init();
