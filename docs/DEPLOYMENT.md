# 部署指南 · GOTY 知识图谱

本项目有**两种形态**，部署方式完全不同，按你要提供的能力选择：

| 形态 | 能力 | 需要后端 | 适用场景 |
|------|------|---------|---------|
| **静态首页**（v1） | 力导向图浏览、表格、节点洞察 | ❌ 不需要 | GitHub Pages / 任意静态托管，纯展示 |
| **完整站点** | 上面全部 + 参数化探索板块 + 交互式图谱浏览器 | ✅ FastAPI | 自己跑服务，需要探索能力 |

---

## 一、静态首页部署（GitHub Pages）

### 能做什么、不能做什么

`site/index.html` 是**自包含**的：数据内联（约 189KB）、零外链、vis-network 已本地化，总共约 884KB。因此可以直接放到任何静态托管上。

但**探索功能不可用**——探索 SPA（`site/explorer-graph/`）依赖 FastAPI 后端（`app.js` 里 `const API = "/api"`），静态托管无法提供。

> 首页的「开始数据探索」入口默认 `display:none`，只有探测到 `/api/meta` 返回 `exploration_enabled` 才显示，探测失败静默隐藏。所以静态部署下**不会出现死链**。

### 自动发布（推荐）

仓库已内置 `.github/workflows/pages.yml`，push 到 main 时自动发布。

**只需在仓库侧开启一次**：
`Settings → Pages → Source` 选 **GitHub Actions**（不是 "Deploy from a branch"）。

流程会：
1. `npm run build:css` 重新构建样式
2. **校验产物与源同步**（不同步直接失败，避免发布过期样式）
3. 组装 `_site/{index.html, assets/*, .nojekyll}`
4. 上传并部署

也可在 Actions 页面手动触发（`workflow_dispatch`）。

> 注意：免费私有仓库不支持 Pages，需公开仓库或升级。

### 手动发布

产物只有 3 个文件，拖到任意静态托管即可：

```bash
npm run build:css
mkdir -p _site/assets
cp site/index.html _site/
cp site/assets/index.css _site/assets/
cp site/assets/vis-network.min.js _site/assets/
touch _site/.nojekyll        # 禁用 Jekyll
```

**目录布局不能改**：首页用相对路径引用 `assets/...`，资源必须放在 `assets/` 子目录下，平铺会导致子路径部署（`user.github.io/<repo>/`）时 404。

---

## 二、完整站点部署（含探索后端）

### Docker（推荐）

```bash
cp .env.sample .env          # 按需修改
docker-compose up -d
```

`docker-compose` 会在 Neo4j 健康检查通过后由 `importer` 服务自动导入数据集。

### 本地 / 裸机

```bash
uv sync
uv run uvicorn api.app:app --host 0.0.0.0 --port 8000
```

### 部署必读

- **HTTPS 是硬性要求**：账号体系依赖 TLS 承载，生产请把 `GOTY_SESSION_COOKIE_SECURE=true`。明文 HTTP 下会话 Cookie 会被窃听。
- **邮箱验证**：默认开启「注册邮箱必填 + 验证前禁登录」。生产必须配好 `GOTY_MAIL_MODE=smtp` 与 `GOTY_APP_PUBLIC_URL`，否则新用户收不到验证邮件、会被永久锁在登录外。详见 [docs/EMAIL_VERIFICATION.md](EMAIL_VERIFICATION.md)。
- **生产务必保持 `GOTY_AUTH_ENABLED=true`**。

---

## 三、免登录模式（调试 / 内网演示）

设 `GOTY_AUTH_ENABLED=false` 即进入「整个站点免登录」：

| 能力 | 行为 |
|------|------|
| `/explore` 与所有只读接口 | 匿名直接放行，不再跳登录页 |
| `POST /api/jobs`、`POST /api/board/{name}` | 免登录（任务归属记为匿名） |
| 登录 / 注册入口 | 隐藏，`/login` 显示「登录已关闭」 |
| `/api/meta` | `auth_enabled=false`，前端据此隐藏用户区并跳过登录态请求 |

仅用于本地调试或内网演示。

> **边界**：`auth_enabled=true` + `explore_requires_auth=false` 只免**页面**，API 仍返回 401。若你想「保留账号体系 + 匿名可用全部功能」，当前没有对应开关。

---

## 四、可选图后端（Neo4j）

默认用内存 networkx，无需 Neo4j。想走真实图库时设 `GOTY_GRAPH_BACKEND=neo4j`。

- 本地起容器：`make neo4j`（7474/7687）或 `make neo4j-dev`（7475/7688，不抢占本机已有实例）
- 连不上时只读接口返回 503，不会静默回退
- 双后端一致性由 `tests/integration/test_graph_backends.py` 守护
- 详见 [docs/neo4j_tutorial.md](neo4j_tutorial.md)

**macOS 注意**：`scripts/*.sh` 里 `$VAR` 后若紧跟中文必须写成 `${VAR}`。系统自带 bash 3.2 会把多字节字符首字节并入变量名，导致 `set -u` 报 unbound variable。

---

## 五、前端样式构建

样式使用 **Tailwind v4**，源在 `site/src/`，产物在 `site/assets/`：

```bash
npm run build:css     # 构建两个产物
make css              # 等价
npm run watch:css     # 开发期监听
```

**产物已随仓库提交**，因此**部署与运行时不需要 Node**——只有改动样式时才需要构建。

**改了样式必须提交产物**。`make css-check`（已接入 `make ci`）会重新构建并校验产物与源是否同步，不同步就失败。

### 为什么是两个产物

两个页面的 `:root` 变量命名体系不同，有 5 个同名不同值（`--bg/--line/--muted/--panel/--panel-2`），合进一个产物会互相覆盖。而 v1 首页是**刻意保留的原始页**（约定：落地页必须是它），不能被探索页的样式污染。

| 入口 | 产物 | 服务页面 |
|------|------|---------|
| `site/src/tailwind.css` | `site/assets/tailwind.css` | 探索 SPA `/explore` |
| `site/src/index.css` | `site/assets/index.css` | v1 首页 `/` |

共享层 `site/src/base.css` 含 Tailwind 基础、iOS `100dvh`、触摸目标 44px 等。

---

## 相关文档

| 文档 | 内容 |
|------|------|
| [docs/ARCHITECTURE.md](ARCHITECTURE.md) | 后端架构、分层、异步任务、认证分层 |
| [docs/CONFIGURATION.md](CONFIGURATION.md) | 全部 `GOTY_*` 环境变量参考 |
| [docs/SECURITY.md](SECURITY.md) | 防护、审计、访问控制、认证体系 |
| [docs/EMAIL_VERIFICATION.md](EMAIL_VERIFICATION.md) | 邮箱验证流程与配置 |
| [docs/EXPLORATION.md](EXPLORATION.md) | 探索功能使用指南 |
| [docs/neo4j_tutorial.md](neo4j_tutorial.md) | Neo4j 接入与示例查询 |
