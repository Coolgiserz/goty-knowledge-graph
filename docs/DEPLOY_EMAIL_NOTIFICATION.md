# 部署结果邮件通知

`pages.yml` 在部署结束后（**成功和失败都会**）给指定邮箱发一封邮件。用 `dawidd6/action-send-mail`
发送，SMTP 走你自己的企业邮箱。

- 未配置 secrets 时**自动跳过**，不会让 CI 变红——fork 本仓库的人没有 secrets 也能正常跑。
- 邮件含：仓库 / 分支 / 提交号 / 提交说明 / 触发者 / 时间 / 线上地址 / Actions 运行链接。

---

## 一、你需要配置的 6 个 Secrets

路径：**仓库 → Settings → Secrets and variables → Actions → Secrets → New repository secret**

6 个全都是必填——少任何一个都会走「跳过邮件通知」分支或直接报错。

| Secret 名 | 必填 | 填什么 | 示例 |
|---|---|:---:|---|
| `MAIL_SERVER` | ✅ | SMTP 服务器地址 | `smtp.exmail.qq.com` |
| `MAIL_PORT` | ✅ | 端口：`465` 或 `587` | `465` |
| `MAIL_USERNAME` | ✅ | 邮箱账号（通常就是完整邮箱地址） | `ci@yourcompany.com` |
| `MAIL_PASSWORD` | ✅ | 邮箱密码，或**客户端专用密码/授权码** | `AbcD1234...` |
| `MAIL_FROM` | ✅ | 发件人，**必须是纯邮箱或 `显示名 <邮箱>` 两种格式之一** | `GOTY Bot <ci@yourcompany.com>` |
| `MAIL_TO` | ✅ | 收件人，多个用英文逗号分隔 | `you@yourcompany.com` |

> 一共 6 个（`MAIL_FROM` 也必填）。**`MAIL_FROM` 写错了整个 action 会直接报错**，见第四节坑 1。

### 可选的 Variables（一般不需要动）

路径：**Settings → Secrets and variables → Actions → Variables**

| Variable 名 | 默认 | 用途 |
|---|---|---|
| `PRODUCTION_URL` | 无 | 正式域名，写进邮件正文。不填则显示「未配置」 |
| `MAIL_IGNORE_CERT` | `false` | 自签证书的企业 SMTP 才设 `true`（会降级 TLS 校验，慎用） |

**不需要配 `secure`**——action 内部按端口自动判断：465 走隐式 TLS，587 走明文连接后
STARTTLS。这正是只让你填端口的原因。

---

## 二、465 还是 587？企业 SMTP 端口怎么选

| 端口 | 加密方式 | 什么时候用 | 本 action 的行为 |
|---|---|---|---|
| **465** | 隐式 TLS（连上就是 TLS） | 传统企业邮箱首选 | 自动 `secure=true` ✅ |
| **587** | 明文连接 → STARTTLS 升级 | 现代邮件服务（Microsoft 365 等）的提交端口 | 自动 `secure=false`，服务器支持 STARTTLS 时自动升级 ✅ |
| 25 | 通常不加密 | 云厂商默认封禁 25 出站，一般别用 | — |

**两个都试一遍是最快的判断方式**：配 465 跑一次，不行就改 587。

常见服务商的 SMTP 地址（**请与你邮箱服务商的官方帮助文档核对，以下仅为常见默认值，我不能保证长期不变**）：

| 服务商 | 服务器 | 端口 |
|---|---|---|
| 腾讯企业邮 | `smtp.exmail.qq.com` | 465 / 587 |
| 阿里云企业邮 | `smtp.qiye.aliyun.com`（万网邮：`smtp.mxhichina.com`） | 465 / 587 |
| 网易企业邮 | `smtp.qiye.163.com` | 465 / 994 |
| Microsoft 365 / Exchange Online | `smtp.office365.com` | **587**（且要求 STARTTLS） |
| Gmail | `smtp.gmail.com` | 465 / 587（须用应用专用密码） |
| 腾讯云 SES | `smtp.qcloudmail.com` | 465 / 587 |
| 自建 Postfix / Exchange | 问你的 IT | 看配置 |

---

## 三、测试方式

不需要真的改代码推 main——用手动触发：

1. **Actions** → 左侧选 **Deploy static site to Pages**
2. 右上角 **Run workflow** → 选 `main` → **Run workflow**
3. 跑完看 `Email notification` job：
   - 日志里 `----- subject -----` / `----- body -----` 会打印邮件内容，**不发信也能确认内容对不对**
   - 收到邮件 = 配置成功

⚠️ 如果日志里出现 「未配置 MAIL_SERVER / MAIL_TO，跳过邮件通知」 → secrets 名写错了，回去核对拼写
（大小写敏感）。

---

## 四、四个真实的坑（都是读过 action 源码确认的）

### 坑 1：`MAIL_FROM` 必须是两种格式之一

action 会用 nodemailer 的 `addressparser` 校验 `from`，不匹配就直接抛错
`'from' address is invalid`。

| 写法 | 结果 |
|---|---|
| `ci@yourcompany.com` | ✅ |
| `GOTY Bot <ci@yourcompany.com>` | ✅ |
| `GOTY Bot` | ❌ 报错 |
| `<ci@yourcompany.com>` | ❌ 报错（没有显示名时不要尖括号） |

**另外**：多数企业邮箱要求发件人与认证账号一致，`MAIL_FROM` 里的邮箱地址最好**等于**
`MAIL_USERNAME`，否则可能被服务器以 `553 Mail from must equal authorized user` 拒绝。

### 坑 2：绝不要开 `nodemailerdebug` / `nodemailerlog`

这两个开关会把完整 SMTP 对话打进 Actions 日志，其中**包含 AUTH 阶段的 base64 凭据**
（base64 可逆，等于明文密码）。

本仓库是**公开仓库，Actions 日志人人可见**。开了等于把企业邮箱密码挂在网上。
本 workflow 没有启用这两个开关——**也请不要为了排查问题临时打开**。

### 坑 3：不要用 `connection_url` 的 `smtp+starttls://` 形式

这是上游 v18 的实现 bug。看它的 `main.js`：

```js
case "smtp+starttls:":
    serverPort = "465";
    secure = "true";   // ← 问题在这
    break;
```

`smtp+starttls` 本意是「明文连接后升级 TLS」，但它把 `secure` 设成了 `true`（隐式 TLS）。
结果：你在 587 端口上做 TLS 握手，而 587 期望的是先明文对话 —— **必然连不上**。

反直觉的是：用 `smtp://host:587` 反而是对的（该分支设 `secure=false`，nodemailer 会
opportunistic STARTTLS）。

本 workflow 用的是 `server_address` + `server_port`，绕开了这两个分支，所以不受影响。
**如果你以后自己改配置，别图省事换 `connection_url`。**

### 坑 4：邮件进了垃圾箱

用企业邮箱发一般没问题。如果进了垃圾箱，检查发件域的 SPF / DKIM / DMARC 记录——
这属于邮件服务器侧配置，GitHub Actions 管不了。

---

## 五、故障排查

| Actions 日志里的错误 | 原因 | 解法 |
|---|---|---|
| `Server address must be specified` | `MAIL_SERVER` 没配或拼错 | 核对 secret 名 |
| `'from' address is invalid` | 发件人格式不对 | 见坑 1 |
| `Invalid login` / `535 Authentication failed` | 账号密码错，或需要用**客户端专用密码** | 企业邮箱常需要在后台单独开启 SMTP 并生成专用密码 |
| `wrong version number` / TLS 握手失败 | 端口与加密方式不匹配 | 465 ⇄ 587 换一个 |
| `self signed certificate` | 企业 SMTP 用自签证书 | 设 Variable `MAIL_IGNORE_CERT=true`（会降级安全校验） |
| `553 Mail from must equal authorized user` | 发件人与认证账号不一致 | `MAIL_FROM` 的邮箱地址改成 `MAIL_USERNAME` |
| `ETIMEDOUT` / `ECONNREFUSED` | 端口不通 | 确认服务器出站没被防火墙拦；确认没在用 25 |
| 日志显示「跳过邮件通知」 | secrets 未配置 | 这是**预期行为**，不是故障 |

---

## 六、想改成「只在成功时发」

把 `pages.yml` 里 notify job 的条件从：

```yaml
if: ${{ always() && !cancelled() }}
```

改成：

```yaml
if: ${{ needs.deploy.result == 'success' }}
```

失败就不发信了。**个人建议保留失败通知**——部署挂了而你不知道，比多收一封邮件代价大得多。

---

## 七、实现要点（改这个文件时看的）

- **pin 到 commit SHA 而不是 tag**：`dawidd6/action-send-mail@94de994a9f6fffee200243214e17002e2920bb59 # v18`。
  这个 action 会拿到邮箱密码；tag 理论上可被移动，SHA 不可变。
- **主题和正文都写进文件，再用 `file://` 交给 action**：多行文本塞进 YAML 极易被缩进和转义搞坏。
- **提交信息通过 `env` 传入，不内联进 shell**：`${{ github.event.head_commit.message }}`
  是用户可控内容，内联 `run` 是 GitHub Actions 经典的脚本注入点。
- **取第一行用参数展开 `${VAR%%$'\n'*}` 而非 `printf | head -1`**：后者在超长提交信息
  （超过 pipe buffer，Linux 64KB）时会让 printf 收到 SIGPIPE，被 `set -o pipefail` 捕获后
  `set -e` 终止脚本。实测 439KB 稳定复现。
