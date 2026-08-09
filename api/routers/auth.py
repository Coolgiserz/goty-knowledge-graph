"""认证路由：注册 / 登录 / 登出 / 当前用户；以及内置登录页 ``/login``。

- ``POST /api/auth/register`` 自助注册（``GOTY_AUTH_REGISTRATION_OPEN=false`` 时整体 403）。
- ``POST /api/auth/login``    校验凭据并写入会话 Cookie。
- ``POST /api/auth/logout``   吊销会话并清除 Cookie。
- ``GET  /api/auth/me``       返回当前登录用户（未登录 401）。
- ``GET  /login``             内置登录/注册页（由 ``api.app`` 挂载）。

会话为服务端存储，Cookie 仅持随机会话 id（HttpOnly + SameSite=Lax）。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..auth.deps import get_current_user, get_user_store
from ..auth.models import User
from ..auth.session import clear_session_cookie, set_session_cookie
from ..auth.store import UserStore
from ..config import Settings
from ..deps import get_settings_dep

router = APIRouter(prefix="/api/auth", tags=["auth"])

# 用户名规则：3-32 位，字母/数字/._-/，避免空白与特殊字符注入。
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
# 邮箱规则（基础格式校验：含 @ 且 @ 前后及域名含点；非 RFC 全量，足够挡住明显错误）。
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# 密码规则：至少 8 位，且同时包含字母与数字（基本强度要求，挡住弱口令）。
PASSWORD_MIN_LEN = 8
PASSWORD_RE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d).+$")


class RegisterRequest(BaseModel):
    username: str
    password: str
    email: str = ""


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    email: str


def _user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, email=u.email or "")


@router.post("/register")
async def register(
    req: RegisterRequest,
    request: Request,
    response: Response,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    if not settings.auth_registration_open:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="registration_closed")
    if not USERNAME_RE.match(req.username):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_username")
    if (
        not req.password
        or len(req.password) < PASSWORD_MIN_LEN
        or not PASSWORD_RE.match(req.password)
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="weak_password")
    if req.email and not EMAIL_RE.match(req.email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid_email")
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="auth_store_unavailable"
        )
    try:
        u = await store.register(req.username, req.password, req.email)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="username_taken") from None
    # 注册成功即自动登录（写入会话 Cookie）
    ip = request.client.host if request.client else ""
    sid = await store.create_session(u.id, ip, settings.session_ttl_seconds)
    set_session_cookie(
        response,
        settings.session_cookie_name,
        sid,
        settings.session_ttl_seconds,
        settings.session_cookie_secure,
    )
    return _user_out(u)


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="auth_store_unavailable"
        )
    u = await store.authenticate(req.username, req.password)
    if u is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")
    ip = request.client.host if request.client else ""
    sid = await store.create_session(u.id, ip, settings.session_ttl_seconds)
    set_session_cookie(
        response,
        settings.session_cookie_name,
        sid,
        settings.session_ttl_seconds,
        settings.session_cookie_secure,
    )
    return _user_out(u)


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    store: UserStore | None = Depends(get_user_store),
    settings: Settings = Depends(get_settings_dep),
):
    sid = request.cookies.get(settings.session_cookie_name)
    if store is not None and sid:
        await store.delete_session(sid)
    clear_session_cookie(response, settings.session_cookie_name)
    return {"ok": True}


@router.get("/me")
async def me(current_user: User | None = Depends(get_current_user)):
    if current_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication_required"
        )
    return _user_out(current_user)


# ---------------------------------------------------------------------------
# 内置登录 / 注册页（静态 HTML，无外部依赖；同源 Cookie 随请求自动携带）
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
  .tab { flex: 1; padding: 8px; text-align: center; border-radius: 8px; cursor: pointer;
    background: #334155; color: #cbd5e1; font-size: 14px; user-select: none; }
  .tab.active { background: #2563eb; color: #fff; }
  label { display: block; font-size: 13px; margin: 12px 0 6px; color: #cbd5e1; }
  input { width: 100%; padding: 10px 12px; border-radius: 8px; border: 1px solid #475569;
    background: #0f172a; color: #e2e8f0; font-size: 14px; }
  input:focus { outline: none; border-color: #2563eb; }
  .hint { font-size: 12px; color: #64748b; margin-top: 5px; line-height: 1.4; }
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
    <div class="tabs">
      <div class="tab active" id="tab-login" onclick="switchTab('login')">登录</div>
      <div class="tab" id="tab-register" onclick="switchTab('register')">注册</div>
    </div>

    <div class="panel active" id="panel-login">
      <label for="l-user">用户名</label>
      <input id="l-user" autocomplete="username" placeholder="用户名" />
      <div class="field-err" id="err-l-user"></div>
      <label for="l-pass">密码</label>
      <input id="l-pass" type="password" autocomplete="current-password" placeholder="密码" />
      <div class="field-err" id="err-l-pass"></div>
      <button class="submit" onclick="doLogin()">登录</button>
    </div>

    <div class="panel" id="panel-register">
      <label for="r-user">用户名</label>
      <input id="r-user" autocomplete="username" placeholder="用户名" />
      <div class="field-err" id="err-r-user"></div>
      <label for="r-email">邮箱（可选）</label>
      <input id="r-email" autocomplete="email" placeholder="you@example.com" />
      <div class="field-err" id="err-r-email"></div>
      <label for="r-pass">密码</label>
      <input id="r-pass" type="password" autocomplete="new-password" placeholder="密码" />
      <div class="field-err" id="err-r-pass"></div>
      <div class="hint">密码至少 8 位，且需同时包含字母和数字。</div>
      <button class="submit" onclick="doRegister()">注册并登录</button>
    </div>

    <div class="msg" id="msg"></div>
  </div>

<script>
  // 与后端一致的校验规则（仅前端体验层，最终以服务端为准）
  var USERNAME_RE = /^[A-Za-z0-9_.-]{3,32}$/;
  var EMAIL_RE = /^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/;
  var PASSWORD_RE = /^(?=.*[A-Za-z])(?=.*\\d).+$/;

  function switchTab(name) {
    document.getElementById("tab-login").classList.toggle("active", name === "login");
    document.getElementById("tab-register").classList.toggle("active", name === "register");
    document.getElementById("panel-login").classList.toggle("active", name === "login");
    document.getElementById("panel-register").classList.toggle("active", name === "register");
    clearAllErrors();
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
  function doLogin() {
    clearAllErrors();
    var username = document.getElementById("l-user").value.trim();
    var password = document.getElementById("l-pass").value;
    if (!username) { setFieldErr("err-l-user", "请输入用户名"); return; }
    if (!password) { setFieldErr("err-l-pass", "请输入密码"); return; }
    fetch("/api/auth/login", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({ username: username, password: password })
    }).then(function (r) {
      if (r.ok) { location.href = nextUrl(); return; }
      return r.json().then(function (j) { setMsg(zhError(j.detail), "err"); });
    }).catch(function () { setMsg("网络错误，请稍后再试", "err"); });
  }
  function doRegister() {
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
    fetch("/api/auth/register", {
      method: "POST", headers: {"Content-Type": "application/json"},
      credentials: "same-origin",
      body: JSON.stringify({ username: username, email: email, password: password })
    }).then(function (r) {
      if (r.ok) { location.href = nextUrl(); return; }  // 注册成功即自动登录（已写会话 Cookie）
      return r.json().then(function (j) {
        // 把服务端错误归位到对应字段（重名 / 邮箱 / 密码等）
        var d = j.detail;
        if (d === "username_taken") setFieldErr("err-r-user", zhError(d));
        else if (d === "invalid_email") setFieldErr("err-r-email", zhError(d));
        else if (d === "invalid_username") setFieldErr("err-r-user", zhError(d));
        else if (d === "weak_password") setFieldErr("err-r-pass", zhError(d));
        else setMsg(zhError(d), "err");
      });
    }).catch(function () { setMsg("网络错误，请稍后再试", "err"); });
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
</script>
</body>
</html>
"""


def login_page() -> HTMLResponse:
    """返回内置登录页（由 ``api.app`` 以 ``GET /login`` 挂载）。"""
    return HTMLResponse(LOGIN_PAGE_HTML)


# 认证关闭（全部免登录调试模式）时访问 /login 的提示页：不渲染任何登录/注册表单，
# 引导用户直接回到首页。避免界面出现「不该有的」登录/注册入口。
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


def login_page_disabled() -> HTMLResponse:
    """认证关闭时返回「登录已关闭」提示页（无登录/注册表单）。"""
    return HTMLResponse(LOGIN_DISABLED_HTML)
