# 多途径登录功能：需求与设计（Plan）

本文档为「多途径登录」扩展的 Plan，与 [design-login.md](design-login.md) 阶段 1 并存；格式遵循 [docs/README.md](../README.md) 与 [design/_template-plan.md](_template-plan.md)。

---

## 1. 目标与范围

| 项目 | 说明 |
|------|------|
| 目标 | 在保留现有「用户名+密码」登录基础上，增加三种登录方式：**手机验证码**、**微信扫码**、**QQ 扫码**，用户可任选一种完成登录；**后续支持绑定**，使手机/微信/QQ 绑定到同一账号，实现多端登录对应一个账号。 |
| 本阶段范围 | **包含**：发送/校验手机验证码登录、微信/QQ 扫码登录；登录页 Tab 或入口切换；与现有 JWT/me 兼容；用户表预留 phone/wechat_openid/qq_openid 以支持后续绑定。**不包含**：本阶段不做「绑定已有账号」入口与解绑管理（后续迭代实现）。 |
| 依赖/前置 | 阶段 1 登录（design-login.md）、现有 users 表与 JWT 鉴权、前端登录页与主题。 |

---

## 2. 功能需求

| 登录方式 | 必须行为 | 可选/后续 |
|----------|----------|------------|
| 手机验证码 | 输入手机号 → 发送验证码（节流）→ 输入验证码 → 提交登录；验证码有效期（如 5 分钟）、长度（如 6 位）；若手机号未注册则自动创建用户。 | **后续**：在「个人中心」等入口将手机号绑定到当前账号，实现手机号登录即进入该账号。 |
| 微信扫码 | 展示微信二维码 → 用户扫码并确认 → 用 openid 查/建用户并签发 JWT；未绑定则自动创建用户。 | **后续**：绑定微信到当前账号，微信扫码登录即进入该账号。 |
| QQ 扫码 | 同微信流程；使用 QQ 互联 openid 查/建用户并签发 JWT。 | **后续**：绑定 QQ 到当前账号，QQ 扫码登录即进入该账号。 |

**多端对应一账号**：后续通过「绑定」能力，将同一用户的手机、微信、QQ 绑定到同一个 user 记录，则无论用哪种方式登录都会进入该账号。

交互与体验：

- 登录页提供 Tab 或明显入口：账号密码 / 手机验证码 / 微信扫码 / QQ 扫码；风格与现有登录页、主题一致。
- 错误提示统一：验证码错误/过期、第三方取消授权、网络错误等有明确文案。
- 手机验证码：发送后倒计时（如 60s）防重复点击；同一手机号节流（如 1 分钟 1 次）。

---

## 3. 接口约定

除现有 `POST /api/auth/login`、`GET /api/auth/me` 等外，新增如下接口。认证方式仍为 JWT，前端存 `panel_token`，请求带 `Authorization: Bearer <token>`。

### 3.1 手机验证码

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/auth/sms/send | 请求体 `{ "phone": "13800138000" }`；校验手机号格式；发送验证码（或 mock）；同一手机号 60s 内仅可发送一次；成功返回 `{ "message": "验证码已发送" }`，失败 400/429。 |
| POST | /api/auth/sms/login | 请求体 `{ "phone": "13800138000", "code": "123456" }`；校验验证码正确且未过期；若用户不存在则自动创建（username 可为 `phone_13800138000` 或配置前缀）；成功返回同 login：`{ token, user: { username } }`，失败 401。 |

手机号格式：11 位大陆手机号（可放宽为 1 开头 11 位数字）。验证码：6 位数字，有效期 5 分钟；存储于内存/Redis/数据库均可，本阶段可用内存 + 过期清理。

### 3.2 微信扫码

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/auth/wechat/qr | 无参或带 state；返回 `{ "qr_url": "https://...", "state": "xxx" }` 或前端可用的授权 URL；前端用此 URL 生成二维码供用户扫描。 |
| GET 或 POST | /api/auth/wechat/callback | 微信回调或前端轮询用；参数含 code/state；后端用 code 换 openid（及可选 unionid）；以 openid 查用户，无则创建（username 如 `wx_<openid 后缀>`）；签发 JWT，返回 `{ token, user: { username } }` 或重定向到前端带 token 的 URL。 |

说明：微信网站应用扫码为 OAuth2；具体为获取临时 code 后服务端用 code + app_secret 换 access_token 与 openid。若采用前端轮询「是否已扫码并确认」，则需后端提供「用 code 换 token」的接口供前端在拿到 code 后调用。

### 3.3 QQ 扫码

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/auth/qq/url | 返回 QQ 互联授权 URL：`{ "auth_url": "https://..." }`；前端打开新窗口或展示二维码（QQ 支持 redirect 或 callback）。 |
| GET 或 POST | /api/auth/qq/callback | 参数含 code；用 code 换 access_token 再换 openid；以 openid 查/建用户，签发 JWT，返回同上。 |

（QQ 互联流程与微信类似，均为 OAuth2；callback 可为重定向或 JSON 依实现而定。）

---

## 4. 数据与存储

### 4.1 用户表扩展（users）

在现有 `id, username, password_hash, created_at, updated_at` 基础上增加可选字段（可分批迁移）：

| 字段 | 类型 | 说明 |
|------|------|------|
| phone | VARCHAR(20) NULL UNIQUE | 手机号，唯一；一个手机号只能绑定一个账号，便于后续「绑定手机」时写入当前用户。 |
| wechat_openid | VARCHAR(64) NULL UNIQUE | 微信 openid；一个 openid 只能绑定一个账号。 |
| qq_openid | VARCHAR(64) NULL UNIQUE | QQ openid；一个 openid 只能绑定一个账号。 |

说明：同一用户可同时拥有 phone、wechat_openid、qq_openid（以及 password_hash），实现**多端登录对应一个账号**；`password_hash` 可为空或占位（纯第三方/手机用户无密码时）。

### 4.2 验证码存储

- 键：手机号；值：验证码、过期时间。
- 实现：内存字典 + 定时清理过期项即可；若需多实例部署可后续改为 Redis。

### 4.3 配置项（环境变量）

- 手机验证码：`SMS_PROVIDER`（如 `mock` / `aliyun`）；**先做 mock**，不依赖阿里云等；mock 时固定验证码（如 123456）便于开发与测试。
- **微信真实扫码**（不配置则走 mock）：
  - `WECHAT_APP_ID`、`WECHAT_APP_SECRET`：微信开放平台「网站应用」的 AppID 与 AppSecret。
  - `WECHAT_REDIRECT_URI`：后端回调完整 URL，需与开放平台中「授权回调域」下可访问的地址一致，例如 `http://你的域名/api/auth/wechat/callback`；本地开发可用 `http://localhost:8000/api/auth/wechat/callback`（需在微信后台配置该域）。
- **QQ 真实扫码**（不配置则走 mock）：
  - `QQ_APP_ID`、`QQ_APP_KEY`：QQ 互联「网站应用」的 appid 与 appkey。
  - `QQ_REDIRECT_URI`：后端回调完整 URL，需与 QQ 互联后台「回调地址」一致，例如 `http://你的域名/api/auth/qq/callback`。
- `FRONTEND_ORIGIN`：扫码/QQ 授权成功后跳转的前端地址，默认 `http://localhost:5173`；生产环境改为实际前端域名。

配置示例（`backend/.env`，可选）：

```env
# 微信开放平台 - 网站应用
WECHAT_APP_ID=wxxxxxxxxx
WECHAT_APP_SECRET=xxxxxxxx
WECHAT_REDIRECT_URI=http://localhost:8000/api/auth/wechat/callback

# QQ 互联 - 网站应用
QQ_APP_ID=1xxxxxxxxxx
QQ_APP_KEY=xxxxxxxxxxxxxxxx
QQ_REDIRECT_URI=http://localhost:8000/api/auth/qq/callback

# 前端地址（扫码/QQ 回调后跳转）
FRONTEND_ORIGIN=http://localhost:5173
```

微信/QQ 后台均需配置与上述一致的「授权回调域」或「回调地址」。

### 4.4 唯一 username 技术路径（参考选型）

为保证自动创建用户时 `username` 在表内唯一（表有 `UNIQUE` 约束），可采用以下任一或组合方式。

| 路径 | 思路 | 生成规则示例 | 优点 | 缺点 |
|------|------|----------------|------|------|
| **A. 前缀 + 业务唯一标识** | 用「来源 + 平台唯一 ID」生成，天然不重复。 | 手机：`phone_13800138000`<br>微信：`wx_` + openid（或后 8 位）<br>QQ：`qq_` + openid（或后 8 位） | 实现简单；手机号/openid 本身唯一，几乎无冲突。 | 可读性一般；openid 较长时需截断，理论上有极低概率碰撞（可再加后缀）。 |
| **B. 前缀 + 标识 + 冲突重试** | 同上，但表设 `username UNIQUE`，插入时若发生唯一约束冲突则重试。 | 首试：`phone_13800138000`；冲突则 `phone_13800138000_1` 或 `phone_13800138000_a3f2`（随机后缀）。 | 保证一定生成成功；适合 openid 被截断导致碰撞的兜底。 | 需捕获 IntegrityError 并重试，代码略多。 |
| **C. 统一唯一 ID（UUID/雪花）** | username 不体现来源，用全局唯一 ID 生成。 | `u_` + UUID 前 12 位，或 `u_` + 雪花 ID 转短码。 | 绝对唯一、实现统一；不暴露手机号/openid。 | 不可读、不可记；用户若需「登录名」需另做昵称/手机展示。 |
| **D. 昵称与登录名分离（后续扩展）** | 登录用 `phone`/`wechat_openid`/`qq_openid` 查用户，`username` 仅作展示或可选。 | 表内用 id/openid/phone 做主登录键；`username` 或 `display_name` 可后续让用户修改、或用昵称填充。 | 体验最灵活，适合「昵称 + 多端登录」产品。 | 改动较大，需区分「登录标识」与「展示名」；本阶段可只预留字段。 |

**推荐**：本阶段采用 **A + B 组合**——首选用 **A** 生成（`phone_13800138000`、`wx_<openid 后8位>`、`qq_<openid 后8位>`），插入前或插入时若触发 `username` 唯一约束冲突，则用 **B** 加短随机后缀（如 4 位 hex）再试一次。这样实现简单、可读性尚可，且保证唯一。若你更希望完全不暴露任何业务 ID，可选用 **C**。

### 4.5 后续绑定与账号合并策略（预留）

未绑定即自动创建的账号，后续用户若想「多端对应一个账号」，有两种典型场景，都需要在实现绑定时考虑。

**场景一：仅「绑定到当前账号」（无合并）**

- 用户已用 A 方式登录（如账号密码或已绑定的手机），在个人中心点击「绑定手机/微信/QQ」。
- 后端将当前用户的 `phone` / `wechat_openid` / `qq_openid` 写入即可（当前用户该字段原为空）。
- 若该手机号或 openid **已被其他账号占用**：不能直接写入（违反 UNIQUE）。此时要么提示「该手机/微信/QQ 已绑定其他账号」，要么进入**场景二**由用户选择「合并到当前账号」。

**场景二：账号合并（合并数据库信息）**

- 例如：用户曾用手机号登录 → 自动创建了账号甲（`phone_138xxx`）；又用微信登录 → 自动创建了账号乙（`wx_xxx`）。现在希望两个账号变成一个。
- **处理方式**：以**当前登录的账号为主账号**，把「另一个账号」的数据并过来，再弃用或删除另一个账号。具体：
  1. **主账号**：当前登录用户（如账号甲）。
  2. **被合并账号**：例如账号乙（拥有 `wechat_openid`）。
  3. **数据迁移**：将依赖 `user_id` 的表中「属于账号乙」的记录的 `user_id` 改为账号甲的 id，使所有业务数据归属主账号。当前项目中需迁移的表包括：
     - `servers`（服务器）
     - `site_configs`（站点配置）
     - `monitor_remote_configs`（远程监控配置）
     - `feedback`（反馈）
  4. **标识迁移**：将账号乙的 `phone` / `wechat_openid` / `qq_openid` 中主账号尚未占用的，写入主账号对应字段（主账号已有则跳过）；这样之后用手机/微信/QQ 登录会直接命中主账号。
  5. **唯一约束**：把账号乙的 `phone`、`wechat_openid`、`qq_openid` 置为 NULL（或删除账号乙），避免与主账号冲突。
  6. **处置被合并账号**：要么删除账号乙，要么保留但标记为「已合并」且不再用于登录（视是否要保留审计而定）。

**小结**：后续做绑定时，若「要绑定的手机/微信/QQ 已被其他账号占用」，可提示用户是否「合并到当前账号」；若用户确认，则按上述步骤做一次**账号合并**（迁移关联表 + 迁移登录标识 + 置空或删除被合并账号）。本阶段仅预留表结构与设计，不实现绑定与合并接口。

---

## 5. 后端实现要点

- 路由：在 `routers/auth.py` 或拆分为 `auth_sms`、`auth_wechat`、`auth_qq` 子路由，统一挂到 `/api/auth` 下。
- 手机验证码：发送接口节流（按手机号 60s）；校验时从存储中取码比对并检查过期；登录时若用户不存在则 `User(username=..., password_hash=占位, phone=phone)` 并 commit。
- 微信/QQ：使用 httpx 或 requests 请求各平台 token 接口；openid 唯一索引；新用户创建时 username 唯一，按 **4.4 技术路径 A+B**（前缀+标识，冲突则加随机后缀）。
- 安全：state 防 CSRF；code 一次性使用；不把 app_secret 暴露给前端。
- 错误处理：统一返回 400/401 与 `detail` 信息，便于前端展示。

---

## 6. 前端实现要点

- 登录页：Tab 或分段选择「账号密码」「手机验证码」「微信扫码」「QQ 扫码」；默认保留现有账号密码表单。
- 手机验证码：手机号输入框 + 发送验证码按钮（倒计时）+ 验证码输入框 + 登录按钮；调用 `POST /api/auth/sms/send`、`POST /api/auth/sms/login`。
- 微信/QQ：展示二维码（可用 qrcode 库根据 `qr_url`/`auth_url` 生成）；微信可新开窗口授权或内嵌 iframe（视平台要求）；授权成功后拿到 code 或由后端回调重定向到前端并带上 token，前端写入 localStorage 并跳转。
- 风格：与现有 Login.vue、主题变量一致；加载态、错误提示与现有一致。

---

## 7. 与后续阶段衔接

- 与阶段 1 登录、仪表盘、修改密码等完全兼容；`/api/auth/me` 不变，仍返回 `username`。
- **后续绑定**：计划支持在已登录状态下，将手机号/微信/QQ 绑定到当前账号（写入当前用户的 phone/wechat_openid/qq_openid），并支持解绑。绑定后，该手机或第三方登录即进入同一账号，实现**多端登录对应一个账号**。若该手机/openid 已被其他账号占用，可提供「合并到当前账号」选项，按 **4.5 后续绑定与账号合并策略** 做数据迁移与账号合并。当前表结构已预留，无需拆表。
- 其他扩展：多角色等可在本方案基础上再增。

---

## 8. 待产品/环境确认项（请在此确认后开发）

以下项会影响实现细节，请确认或给出选择：

1. **短信服务商**：已确认先做 **mock**（固定验证码如 123456），不依赖阿里云等；后续再对接真实服务商。
2. **微信**：是否已有开放平台「网站应用」的 AppID/AppSecret？开发阶段可 mock 或使用测试号。
3. **QQ**：是否已有 QQ 互联的 appid/appkey？开发阶段可先 mock。
4. **唯一 username**：已在本文档 **4.4 唯一 username 技术路径** 中给出多种实现方式；推荐采用 **A + B 组合**（前缀+标识，冲突时加随机后缀），实现简单且保证唯一。
5. **首次第三方/手机号用户**：自动创建新用户；**后续**再做「绑定已有账号」入口，实现多端登录对应一个账号。

确认后可按本 Plan 实现；短信与微信/QQ 均可先 mock，自动化测试用固定验证码与 mock openid 即可。表结构已按「同一用户可拥有 phone + wechat_openid + qq_openid」设计，便于后续绑定功能接入。

---

## 9. 本阶段完成清单

- [x] 需求与设计评审（含上述确认项）
- [x] 用户表扩展（phone/wechat_openid/qq_openid）及迁移脚本 `migrate_login_multi.py`
- [x] 后端：手机验证码发送与登录接口（mock：固定验证码 123456、60s 节流）
- [x] 后端：微信扫码 GET /api/auth/wechat/qr、GET/POST /api/auth/wechat/callback、GET /api/auth/wechat/poll（支持真实 OAuth + mock）
- [x] 后端：QQ 扫码 GET /api/auth/qq/url、GET/POST /api/auth/qq/callback（支持真实 OAuth + mock）
- [x] 前端：登录页 Tab、手机验证码、微信二维码展示与轮询、QQ 跳转授权、回调 hash 取 token
- [x] **真实扫码**：配置 WECHAT_APP_ID/SECRET/REDIRECT_URI 或 QQ_APP_ID/APP_KEY/REDIRECT_URI 后使用真实微信/QQ 登录；未配置时仍为 mock
- [x] 自动化测试通过
- [ ] 文档与提交说明更新（可按需补充）

---

## 10. 文档更新记录

| 日期 | 变更说明 |
|------|----------|
| 2026-03-14 | 初版：多途径登录（手机验证码、微信、QQ 扫码）需求与接口、数据与前后端要点；待确认项见第 8 节。 |
| 2026-03-14 | 实现：用户表扩展与 migrate_login_multi.py；短信/微信/QQ mock 接口；前端登录页 Tab 与四种方式；自动化测试通过。 |
