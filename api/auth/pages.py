"""登录 / 注册页（内置 HTML，与后端接口代码分离、独立维护）。

- ``GET /login`` 由 ``api.app`` 挂载，渲染此处的 HTML；认证关闭（全部免登录调试模式）
  时改渲染 ``LOGIN_DISABLED_HTML``。
- 页面为静态 HTML + 内联 JS，**无外部依赖、同源 Cookie 随请求自动携带**。
- 校验规则的前端副本（USERNAME_RE / EMAIL_RE / PASSWORD_RE）刻意与后端
  ``api.auth.service`` 的 Python 版本并存：前端只做体验层预校验，后端为最终权威。

如需演进为模板引擎（Jinja2 等），只需替换本模块的实现，路由层无需改动。
"""

from __future__ import annotations

from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------------------
# 登录 / 注册页（认证开启时使用）
# ---------------------------------------------------------------------------

LOGIN_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>登录 · GOTY 知识图谱</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0f172a; color: #e2e8f0;
  }
  .card {
    width: 380px; max-width: 92vw; padding: 28px 26px; border-radius: 14px;
    background: #1e293b; box-shadow: 0 10px 40px rgba(0,0,0,.35);
  }
  h1 { font-size: 19px; margin: 0 0 4px; }
  .sub { font-size: 13px; color: #94a3b8; margin: 0 0 20px; }
  .tabs { display: flex; gap: 8px; margin-bottom: 16px; }
  /* Tab 用 <button> 而非 <div>：原生支持 Tab 聚焦与 Enter/Space 触发（键盘可达）。
     按钮默认样式需重置；聚焦环给键盘用户一个可见反馈。 */
  .tab { flex: 1; padding: 8px; text-align: center; border-radius: 8px; cursor: pointer;
    background: #334155; color: #cbd5e1; font-size: 14px; user-select: none;
    border: 0; font: inherit; }
  .tab.active { background: #2563eb; color: #fff; }
  .tab:focus-visible { outline: 2px solid #60a5fa; outline-offset: 2px; }
  /* 提交期间按钮被禁用时的可见状态（配合 JS 防重复提交） */
  button.submit:disabled { opacity: .6; cursor: default; }
  label { display: block; font-size: 13px; margin: 12px 0 6px; color: #cbd5e1; }
  input { width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #475569;
    background: #0f172a; color: #e2e8f0; font-size: 14px; }
  input:focus { outline: none; border-color: #2563eb; }
  /* #64748b 对卡片底色 #1e293b 仅 3.07:1，不达 WCAG AA（正文需 4.5:1）；
     #94a3b8 为 5.71:1，12px 小字在深底上才可读。 */
  .hint { font-size: 12px; color: #94a3b8; margin-top: 5px; line-height: 1.4; }
  .field-err { font-size: 12px; color: #f87171; margin-top: 5px; min-height: 16px; }
  button.submit { margin-top: 18px; width: 100%; padding: 11px; border: 0; border-radius: 8px;
    background: #2563eb; color: #fff; font-size: 15px; cursor: pointer; }
  button.submit:hover { background: #1d4ed8; }
  .msg { margin-top: 14px; font-size: 13px; min-height: 18px; }
  .msg.err { color: #f87171; }
  .msg.ok { color: #4ade80; }
  .profile-row { display: flex; justify-content: space-between; gap: 12px; font-size: 14px;
    padding: 9px 0; border-bottom: 1px solid #334155; }
  .profile-row:last-of-type { border-bottom: 0; }
  .profile-row span { color: #94a3b8; }
  .profile-row b { font-weight: 600; word-break: break-all; }
  .profile-row b.ok { color: #4ade80; }
  .panel { display: none; }
  .panel.active { display: block; }
</style>
</head>
<body>
  <div class="card">
    <h1>GOTY 知识图谱</h1>
    <p class="sub">数据探索需要登录</p>
    <div class="tabs" role="tablist">
      <button type="button" role="tab" aria-selected="true" class="tab active" id="tab-login"
        onclick="switchTab('login')">登录</button>
      <button type="button" role="tab" aria-selected="false" class="tab" id="tab-register"
        onclick="switchTab('register')">注册</button>
    </div>

      <div class="panel active" id="panel-login" role="tabpanel">
      <label for="l-user">用户名</label>
      <input id="l-user" autocomplete="username" placeholder="用户名" />
      <div class="field-err" id="err-l-user" aria-live="polite"></div>
      <label for="l-pass">密码</label>
      <input id="l-pass" type="password" autocomplete="current-password" placeholder="密码" />
      <div class="field-err" id="err-l-pass" aria-live="polite"></div>
      <button class="submit" onclick="doLogin(this)">登录</button>
      <div class="hint" id="resend-area" style="margin-top:14px;display:none">
        没有收到验证邮件？
        <a href="#" onclick="toggleResend();return false;">重发验证邮件</a>
        <div id="resend-box" style="display:none;margin-top:8px">
          <!-- type=email + aria-label：与登录表单不同，这里没有可见 label，
               仅靠 placeholder 提供无障碍名称（placeholder 不应作为唯一名称来源） -->
          <input id="resend-email" type="email" autocomplete="email"
            aria-label="注册时填写的邮箱" placeholder="注册时填写的邮箱" />
          <button class="submit" style="margin-top:8px" onclick="doResend(this)">发送</button>
          <div class="msg" id="resend-msg" aria-live="polite" style="min-height:16px"></div>
        </div>
      </div>
    </div>

    <div class="panel" id="panel-register" role="tabpanel">
      <label for="r-user">用户名</label>
      <input id="r-user" autocomplete="username" placeholder="用户名" />
      <div class="field-err" id="err-r-user" aria-live="polite"></div>
      <label for="r-email" id="lbl-r-email">邮箱</label>
      <input id="r-email" autocomplete="email" placeholder="you@example.com" />
      <div class="field-err" id="err-r-email" aria-live="polite"></div>
      <div class="hint" id="hint-r-email">用于接收验证邮件；注册后需验证邮箱才能登录。</div>
      <label for="r-pass">密码</label>
      <input id="r-pass" type="password" autocomplete="new-password" placeholder="密码" />
      <div class="field-err" id="err-r-pass" aria-live="polite"></div>
      <div class="hint">密码至少 8 位，且需同时包含字母和数字。</div>
      <button class="submit" onclick="doRegister(this)">注册</button>
    </div>

    <div class="msg" id="msg" aria-live="polite"></div>
  </div>

<script>
  // 与后端一致的校验规则（仅前端体验层，最终以服务端为准）
  var USERNAME_RE = /^[A-Za-z0-9_.-]{3,32}$/;
  var EMAIL_RE = /^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/;
  var PASSWORD_RE = /^(?=.*[A-Za-z])(?=.*\\d).+$/;

  function switchTab(name) {
    // aria-selected 与视觉 active 同步，读屏软件才能感知当前页签
    ["login", "register"].forEach(function (n) {
      var on = (n === name);
      document.getElementById("tab-" + n).classList.toggle("active", on);
      document.getElementById("tab-" + n).setAttribute("aria-selected", on ? "true" : "false");
      document.getElementById("panel-" + n).classList.toggle("active", on);
    });
    clearAllErrors();
  }
  // 提交期间禁用按钮，防止网络慢时重复提交（多点几下 = 多个重复请求/注册）
  function setBusy(btn, busy, text) {
    if (!btn) return;
    btn.disabled = busy;
    if (text) btn.textContent = text;
  }
  function setMsg(text, kind) {
    var el = document.getElementById("msg");
    el.textContent = text || "";
    el.className = "msg" + (kind ? " " + kind : "");
  }
  function setFieldErr(id, text) {
    document.getElementById(id).textContent = text || "";
  }
  function clearAllErrors() {
    ["err-l-user","err-l-pass","err-r-user","err-r-email","err-r-pass"].forEach(function (id) {
      setFieldErr(id, "");
    });
    setMsg("");
  }
  // 服务端错误码 -> 中文提示
  function zhError(detail) {
    var map = {
      "username_taken": "该用户名已被注册，请更换一个",
      "weak_password": "密码至少 8 位，且需同时包含字母和数字",
      "invalid_username": "用户名格式不正确（3-32 位，仅含字母、数字、. _ -）",
      "invalid_email": "邮箱格式不正确",
      "email_required": "请填写邮箱",
      "email_not_verified": "请先验证邮箱后再登录",
      "invalid_or_expired_token": "验证链接无效或已过期",
      "already_verified": "该邮箱已验证",
      "invalid_credentials": "用户名或密码错误",
      "registration_closed": "注册已关闭，暂不支持自助注册",
      "auth_store_unavailable": "认证服务暂时不可用，请稍后再试"
    };
    return map[detail] || ("操作失败：" + (detail || "请重试"));
  }
  function nextUrl() {
    var p = new URLSearchParams(location.search).get("next");
    return (p && p.startsWith("/")) ? p : "/explore/";
  }
  function doLogin(btn) {
    clearAllErrors();
    var username = document.getElementById("l-user").value.trim();
    var password = document.getElementById("l-pass").value;
    if (!username) { setFieldErr("err-l-user", "请输入用户名"); return; }
    if (!password) { setFieldErr("err-l-pass", "请输入密码"); return; }
    setBusy(btn, true, "登录中…");
    fetch("/api/auth/login", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({ username: username, password: password })
    }).then(function (r) {
      if (r.ok) { location.href = nextUrl(); return; }
      return r.json().then(function (j) {
        var d = j.detail;
        if (d === "email_not_verified") {
          // 未验证邮箱：明确提示并露出「重发验证邮件」入口。
          setMsg(zhError(d), "err");
          var ra = document.getElementById("resend-area");
          if (ra) ra.style.display = "block";
          return;
        }
        setMsg(zhError(d), "err");
      });
    }).catch(function () { setMsg("网络错误，请稍后再试", "err"); })
      .finally(function () { setBusy(btn, false, "登录"); });
  }
  function doRegister(btn) {
    clearAllErrors();
    var username = document.getElementById("r-user").value.trim();
    var email = document.getElementById("r-email").value.trim();
    var password = document.getElementById("r-pass").value;
    // 前端预校验（中文提示），后端仍会再次校验
    if (!USERNAME_RE.test(username)) {
      setFieldErr("err-r-user", "用户名需为 3-32 位，仅含字母、数字、. _ -");
      return;
    }
    if (email && !EMAIL_RE.test(email)) {
      setFieldErr("err-r-email", "邮箱格式不正确");
      return;
    }
    if (!password || password.length < 8 || !PASSWORD_RE.test(password)) {
      setFieldErr("err-r-pass", "密码至少 8 位，且需同时包含字母和数字");
      return;
    }
    setBusy(btn, true, "注册中…");
    fetch("/api/auth/register", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({ username: username, email: email, password: password })
    }).then(function (r) {
      if (r.ok) {
        // 硬策略下注册不自动登录（email_verified=false），需先验证邮箱。
        return r.json().then(function (j) {
          if (j.email_verified === false) {
            setMsg("注册成功！请查收验证邮件完成邮箱验证后再登录。", "ok");
            switchTab("login");
            return;
          }
          location.href = nextUrl();  // 软策略：注册即自动登录
        });
      }
      return r.json().then(function (j) {
        // 把服务端错误归位到对应字段（重名 / 邮箱 / 密码等）
        var d = j.detail;
        if (d === "username_taken") setFieldErr("err-r-user", zhError(d));
        else if (d === "invalid_email") setFieldErr("err-r-email", zhError(d));
        else if (d === "email_required") setFieldErr("err-r-email", zhError(d));
        else if (d === "invalid_username") setFieldErr("err-r-user", zhError(d));
        else if (d === "weak_password") setFieldErr("err-r-pass", zhError(d));
        else setMsg(zhError(d), "err");
      });
    }).catch(function () { setMsg("网络错误，请稍后再试", "err"); })
      .finally(function () { setBusy(btn, false, "注册"); });
  }

  function toggleResend() {
    var box = document.getElementById("resend-box");
    if (box) box.style.display = (box.style.display === "none") ? "block" : "none";
  }
  function doResend(btn) {
    var email = document.getElementById("resend-email").value.trim();
    var msg = document.getElementById("resend-msg");
    if (!email) { msg.className = "msg err"; msg.textContent = "请输入邮箱"; return; }
    setBusy(btn, true, "发送中…");
    fetch("/api/auth/request-verification", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({ email: email })
    }).then(function () {
      // 恒返回 200（防枚举），统一提示「若邮箱存在且未验证则已发送」。
      msg.className = "msg ok";
      msg.textContent = "若该邮箱已注册且未验证，验证邮件已（重新）发送，请查收。";
    }).catch(function () { msg.className = "msg err"; msg.textContent = "网络错误，请稍后再试"; })
      .finally(function () { setBusy(btn, false, "发送"); });
  }

  function loadMeta() {
    fetch("/api/meta", { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) return;
      return r.json().then(function (m) {
        // 邮箱是否必填：动态更新标签与提示，并露出「重发验证邮件」。
        if (m.auth_email_required) {
          var lbl = document.getElementById("lbl-r-email");
          if (lbl) lbl.textContent = "邮箱（必填）";
          var hint = document.getElementById("hint-r-email");
          if (hint) hint.textContent = "用于接收验证邮件；注册必填，且需验证后才能登录。";
        }
        var ra = document.getElementById("resend-area");
        if (ra) ra.style.display = "block";  // 验证相关入口默认露出（无害）
      });
    }).catch(function () {});
  }

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  // 已登录用户直接访问 /login：展示个人信息 + 退出登录，而非登录表单
  function checkLoggedIn() {
    fetch("/api/auth/me", { credentials: "same-origin" }).then(function (r) {
      if (!r.ok) return;
      return r.json().then(function (u) {
        var card = document.querySelector(".card");
        if (!card) return;
        card.innerHTML =
          '<h1>GOTY 知识图谱</h1>' +
          '<p class="sub">你已登录</p>' +
          '<div class="profile-row"><span>用户名</span><b>' + escapeHtml(u.username) + '</b></div>' +
          '<div class="profile-row"><span>邮箱</span><b>' + escapeHtml(u.email || "（未填写）") + '</b></div>' +
          '<div class="profile-row"><span>会话状态</span><b class="ok">已登录</b></div>' +
          '<button class="submit" id="logout-now">退出登录</button>';
        var lb = document.getElementById("logout-now");
        if (lb) lb.addEventListener("click", function () {
          fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" })
            .then(function () { location.href = "/login"; })
            .catch(function () { location.href = "/login"; });
        });
      });
    }).catch(function () {});
  }
  checkLoggedIn();
  loadMeta();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# 「登录已关闭」提示页（认证关闭 / 全部免登录调试模式时使用）
# ---------------------------------------------------------------------------

LOGIN_DISABLED_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>登录已关闭 · GOTY 知识图谱</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0f172a; color: #e2e8f0; }
  .card { max-width: 420px; padding: 28px 26px; border-radius: 14px; background: #1e293b;
    box-shadow: 0 10px 40px rgba(0,0,0,.35); text-align: center; }
  h1 { font-size: 19px; margin: 0 0 10px; }
  p { font-size: 14px; color: #94a3b8; line-height: 1.6; }
  a { color: #60a5fa; text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>
</head>
<body>
  <div class="card">
    <h1>登录已关闭（本地调试模式）</h1>
    <p>当前站点已关闭账号体系，无需登录即可使用数据探索。<br />
       <a href="/">返回首页</a></p>
  </div>
</body>
</html>
"""


def login_page() -> HTMLResponse:
    """返回内置登录页（由 ``api.app`` 以 ``GET /login`` 挂载）。"""
    return HTMLResponse(LOGIN_PAGE_HTML)


def login_page_disabled() -> HTMLResponse:
    """认证关闭时返回「登录已关闭」提示页（无登录/注册表单）。"""
    return HTMLResponse(LOGIN_DISABLED_HTML)


# ---------------------------------------------------------------------------
# 邮箱验证确认页（邮件链接落地页：GET /verify-email?token=...）
# ---------------------------------------------------------------------------

VERIFY_EMAIL_PAGE_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>验证邮箱 · GOTY 知识图谱</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: flex; align-items: center; justify-content: center;
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: #0f172a; color: #e2e8f0;
  }
  .card { width: 400px; max-width: 92vw; padding: 28px 26px; border-radius: 14px;
    background: #1e293b; box-shadow: 0 10px 40px rgba(0,0,0,.35); text-align: center; }
  h1 { font-size: 19px; margin: 0 0 12px; }
  .msg { font-size: 14px; line-height: 1.6; margin: 6px 0 18px; }
  .msg.err { color: #f87171; }
  .msg.ok { color: #4ade80; }
  .spin { font-size: 13px; color: #94a3b8; }
  button { padding: 11px 18px; border: 0; border-radius: 8px; background: #2563eb; color: #fff;
    font-size: 15px; cursor: pointer; }
  button:hover { background: #1d4ed8; }
  a { color: #60a5fa; text-decoration: none; }
  a:hover { text-decoration: underline; }
</style>
</head>
<body>
  <div class="card">
    <h1>邮箱验证</h1>
    <div class="msg spin" id="status">正在验证，请稍候…</div>
    <button id="to-login" style="display:none" onclick="location.href='/login'">去登录</button>
  </div>
<script>
  function getToken() {
    return new URLSearchParams(location.search).get("token") || "";
  }
  function show(kind, text) {
    var el = document.getElementById("status");
    el.className = "msg " + kind;
    el.textContent = text;
    if (kind === "ok") document.getElementById("to-login").style.display = "inline-block";
  }
  function verify() {
    var token = getToken();
    // 不向终端用户暴露 token 这类后端术语（项目硬性约定），只描述可执行的下一步。
    if (!token) { show("err", "验证链接不完整，请重新打开邮件中的完整链接。"); return; }
    fetch("/api/auth/verify-email", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({ token: token })
    }).then(function (r) {
      if (r.ok) { show("ok", "邮箱验证成功！现在可以使用该邮箱登录了。"); return; }
      return r.json().then(function (j) {
        var map = {
          "invalid_or_expired_token": "验证链接无效或已过期，请重新获取验证邮件。",
          "already_verified": "该邮箱已验证，直接登录即可。",
          "email_required": "请先填写邮箱。",
          "auth_store_unavailable": "认证服务暂时不可用，请稍后再试。"
        };
        show("err", map[j.detail] || ("验证失败：" + (j.detail || "请重试")));
      });
    }).catch(function () { show("err", "网络错误，请稍后再试。"); });
  }
  verify();
</script>
</body>
</html>
"""


def verify_email_page() -> HTMLResponse:
    """返回邮箱验证确认页（由 ``api.app`` 以 ``GET /verify-email`` 挂载）。"""
    return HTMLResponse(VERIFY_EMAIL_PAGE_HTML)
