# 引擎提交指引（P0）

- 版本：v1.16.7
- 日期：2026-09-02
- 适用站点：https://weirdgiser.site/goty-knowledge-graph/
- 路线：已确定放弃百度自然排名，本指引覆盖 **Bing（优先）+ Google（次要）+ IndexNow（可选）**

---

## 0. 前置状态（已实测，2026-09-02）

| 检查项 | URL | 状态 |
|---|---|---|
| 首页 | `/goty-knowledge-graph/` | HTTP 200，0.58s |
| sitemap.xml | `/goty-knowledge-graph/sitemap.xml` | HTTP 200，内容正确 |
| robots.txt | `/goty-knowledge-graph/robots.txt` | HTTP 200，`User-agent: * / Allow: /` |
| llms.txt | `/goty-knowledge-graph/llms.txt` | HTTP 200 |

三个 SEO 文件已随 PR #3（`feat/seo-geo`）合入 main 并部署，v1.16.7 tag 由 CI 自动打出。

### 复查（2026-09-04）

| 检查项 | 状态 |
|---|---|
| sitemap.xml / robots.txt / llms.txt | 均 HTTP 200 ✅ |
| robots.txt 内容 | `User-agent: *` + `Allow: /` + Sitemap 声明 ✅ |
| `X-Robots-Tag` 响应头 | 无（未阻止索引）✅ |
| `<meta name="robots">` | 无（默认允许索引）✅ |
| DNS 解析 | A 记录 `185.199.110.153`（GitHub Pages）✅ |
| **DNS TXT 记录** | **不存在**——`dig` 与 `nslookup` 交叉验证均为空 → **Google 验证尚未完成**（DNS 通道本身正常，非查询失败） |

> 结论：技术侧全部就绪，**卡在「未验证 / 未提交」这一步**。按下面 §1、§2 执行即可。

---

## 1. Bing Webmaster Tools（最高优先级）

**为什么是 Bing 第一**：大陆可正常访问；是 Windows / Edge 默认搜索；更是 **ChatGPT 搜索、Microsoft Copilot、GitHub Copilot 的索引后端**——投一次同时覆盖传统搜索与 AI 引擎。

### 步骤

1. 打开 https://www.bing.com/webmasters ，用 Microsoft 账号登录
2. 添加站点，输入 `https://weirdgiser.site/goty-knowledge-graph/`
3. **验证方式三选一，推荐 CNAME**：

| 方式 | 做法 | 是否需改代码 | 推荐度 |
|---|---|---|---|
| **CNAME 记录** | DNS 加一条 CNAME（Bing 会给出主机名与 `verify.bing.com` 目标值） | ❌ 不需要 | ⭐ 推荐 |
| HTML meta 标签 | 把 meta 标签塞进 `site/index.html` 的 `<head>` | ✅ 需要走提交流程 | 一般 |
| XML 文件 | 上传验证文件到站点目录 | ✅ 需要部署 | 一般 |

> **选 CNAME 的理由**：本项目是 GitHub Pages 静态站，改 `index.html` 要走 PR 流程、还容易撞上那个 `data-page-node-id` 注入噪声；DNS 验证在域名层面一次完成，零代码改动。

4. 验证通过后，左侧 **Sitemaps** → 提交：
   ```
   https://weirdgiser.site/goty-knowledge-graph/sitemap.xml
   ```
5. 左侧 **URL Submission** → 手动提交首页：
   ```
   https://weirdgiser.site/goty-knowledge-graph/
   ```

---

## 2. Google Search Console（次要但建议做）

Google 在大陆打不开，但海外用户、以及**大量 AI 模型的训练语料**来自 Google 索引，所以仍然值得提交。

### 步骤

1. 打开 https://search.google.com/search-console
2. 添加资源 → 选「**网址前缀**」→ 输入 `https://weirdgiser.site/goty-knowledge-graph/`
3. 验证：在「HTML 文件」方式点**下载此 HTML 验证文件**，得到一个
   `google-site-verification-xxxxxxxx.html`（**文件名不可改**）

4. 把该文件放进仓库 `site/` 目录，并在 `.github/workflows/pages.yml` 的发布清单里补一行：

   ```yaml
   cp site/google-site-verification-*.html _site/
   ```

   提交 → 合并 → 部署后访问
   `https://weirdgiser.site/goty-knowledge-graph/google-site-verification-xxxxxxxx.html`
   确认返回 200，再回 GSC 点「验证」。

5. 验证通过后，左侧 **Sitemap** → 提交：
   ```
   https://weirdgiser.site/goty-knowledge-graph/sitemap.xml
   ```

> **为什么是 HTML 文件而不是 DNS**（2026-09-04 修正）：
> - **Google 的 DNS（TXT/CNAME）验证只支持「域名」资源类型，不支持「网址前缀」资源类型**——两者不可混用。
> - 而本项目的 `weirdgiser.site` 根域上跑着另一个站（Hugo 站「NullSpace」），用「域名」类型会把它一并纳入，且该站的 sitemap 声明的是 `spacetimelab.cn` 的 URL（跨域，无效），会污染 GSC 报错面板。
> - HTML 文件方式只验证这一个路径，且**不需要改 `index.html`**——正好避开 `data-page-node-id` 注入噪声。

---

## 3. IndexNow（可选，单页站点非必需）

### ⚠️ 先说结论：你大概率用不上

IndexNow 的价值是「大量 URL 频繁更新时免等待爬虫」。而这个站是**单页应用，sitemap 里只有 1 个 URL**，数据一年才更新一次。手动在 Bing Webmaster 点一次「URL 提交」就完全够用。

下面这段保留给「以后数据更新想自动化推送」的场景。

### 接入步骤

1. 生成一个 key（8–128 位十六进制字符，仅 `0-9` `a-f`），例如：

   ```bash
   openssl rand -hex 16
   ```

2. 把 key 托管成一个文本文件，key 同时作为**文件名**和**文件内容**：
   - 若你能写域名根目录：`https://weirdgiser.site/{key}.txt`
   - 若只能写子路径（本项目实际是子路径部署）：`https://weirdgiser.site/goty-knowledge-graph/{key}.txt`，靠 `keyLocation` 字段声明

3. 推送：

   ```bash
   curl -s -X POST "https://api.indexnow.org/IndexNow" \
     -H "Content-Type: application/json; charset=utf-8" \
     -d '{
       "host": "weirdgiser.site",
       "key": "你的key",
       "keyLocation": "https://weirdgiser.site/goty-knowledge-graph/你的key.txt",
       "urlList": ["https://weirdgiser.site/goty-knowledge-graph/"]
     }'
   ```

   返回 `200` 即提交成功（`202` 也是接受）。

---

## 4. 怎么确认生效

| 平台 | 工具 | 看什么 |
|---|---|---|
| Bing | URL Inspection | 输入首页 URL，看「已抓取 / 已索引」状态与抓取日期 |
| Google | URL 检查 | 看「是否已编入索引」及抓取时间 |

**耐心提示**：新站点从提交到首次索引，通常 **1–4 周**，Bing 一般比 Google 快。提交后头几天查不到属正常，别反复重提（重复提交不会加速，反而可能被判定为骚扰）。

---

## 5. 坑点清单

1. **验证前先确认 sitemap 真的能访问**——本项目已实测 200，但如果你以后改了 Pages 的部署路径，sitemap 里 `<loc>` 的路径要跟着改，否则提交会被拒。
2. **资源类型与验证方式必须配对**——Google 的「网址前缀」资源只能用 HTML 文件 / HTML 标记 / GA / GTM 验证；「域名」资源才支持 DNS（TXT/CNAME）验证。**两者不能混用**（2026-09-04 修正了本条原先的错误表述）。
3. **坚持用「网址前缀」资源，不要用「域名」资源**——`weirdgiser.site` 根域上另有站点（Hugo 站「NullSpace」，其 canonical 指向 `spacetimelab.cn`），用域名资源会把它一并纳入，干扰判断。
4. **根域 `/sitemap.xml` 是无效的，别被误导**——2026-09-04 实测：`https://weirdgiser.site/sitemap.xml` 返回 200，但里面的 `<loc>` 全是 `spacetimelab.cn/...` 的 URL。**跨域 sitemap 会被 Google 拒绝**。本项目用的是 `/goty-knowledge-graph/sitemap.xml`，不受影响；看到根域那条报错时不必处理它。
5. **Bing 和 Google 可以互相导入**——先做完 GSC，Bing 后台有「从 Google Search Console 导入」按钮，能省一次验证。
6. **提交不等于收录**。索引是搜索引擎的决定，提交只是告诉它"这里有个页面"。
