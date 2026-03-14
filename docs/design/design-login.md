# 登录功能：需求与设计（阶段 1）

本文档为阶段 1 登录部分的 Plan，格式与 [docs/README.md](../README.md) 中「Plan 格式说明」一致。

---

## 1. 目标与范围

| 项目 | 说明 |
|------|------|
| 登录 | 用户名 + 密码，校验通过后进入仪表盘 |
| 登出 | 清除登录态，跳转登录页 |
| 登录态校验 | 未登录访问需鉴权页时重定向到登录页 |
| 错误提示 | 用户名或密码错误时统一提示，不区分具体原因 |

本阶段不实现：找回密码、多用户角色、第三方登录。注册与修改密码已在本阶段完成。**多途径登录**（手机验证码、微信/QQ 扫码）见 [design-login-multi.md](design-login-multi.md)。

---

## 2. 功能需求

功能列表见上表；交互上需错误提示统一、登录态校验与重定向、主题与仪表盘风格一致。

---

## 3. 接口约定

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/auth/login | 请求体 `{ username, password }`，成功返回 `{ token, user: { username } }`，失败 401 |
| POST | /api/auth/logout | 前端删 token 为主，后端返回 `{ message: "ok" }` |
| GET | /api/auth/me | Header `Authorization: Bearer <token>`，返回当前用户 `{ username }`，无效 401 |
| POST | /api/auth/register | 请求体 `{ username, password }`，用户名 2–64 字符、密码至少 6 位；成功返回同 login（token + user），失败 400（如用户名已被使用） |
| POST | /api/auth/change-password | 需登录；请求体 `{ old_password, new_password }`，新密码至少 6 位且不能与原密码相同；成功返回 `{ message: "密码已更新，请使用新密码登录" }`，原密码错误或新密码与原密码相同则 400 |

认证方式：JWT，前端将 token 存 localStorage（键 `panel_token`），请求时带在 Authorization 头。

---

## 4. 数据与存储

- 表 `users`：id, username（唯一）, password_hash, created_at, updated_at。
- 初始管理员：通过 `python -m init_db` 创建（仅当无用户时），用户名 admin，密码来自 `.env` 的 `INIT_ADMIN_PASSWORD`。
- 修改密码：`python change_password.py admin 新密码`（见 README）。

---

## 5. 后端实现要点

- 使用 JWT 签发与校验；auth 路由（login/logout/me/register/change-password）；用户表与 init_db、change_password 脚本；密码哈希存储。

---

## 6. 前端实现要点

- 路由：`/login`、`/register` 公开，`/` 及其子路径需登录；未登录访问需鉴权页重定向到 `/login?redirect=...`。
- 登录页：居中卡片、左右分栏（Logo+品牌 / 表单）、线条背景、上浮标签、主题切换、链接到注册页。
- 注册页：风格与登录页一致，用户名/密码/确认密码，注册成功后写 token 并跳转；链接到登录页。
- 仪表盘：见 `design-dashboard.md`；布局内提供「修改密码」入口，弹窗提交后关闭。

---

## 7. 与后续阶段衔接

- 阶段 2（系统监控）等需登录态；与仪表盘布局、修改密码入口衔接；扩展多用户/角色时在现有 auth 上扩展。

---

## 8. 本阶段完成清单

- [x] 需求明确与详细设计
- [x] 登录页 UI（居中卡片、线条背景、上浮标签、主题切换、Logo）
- [x] 仪表盘占位与路由鉴权
- [x] 后端接口：login / logout / me，JWT
- [x] 用户表与 init_db、change_password
- [x] Windows 本地测试通过（登录 / 登出 / 错误密码 / 主题）
- [ ] WSL 或虚拟机 Linux 环境测试（可选，按 [environment/testing-steps.md](../environment/testing-steps.md) 第六节）
- [ ] Git 提交（建议提交信息：feat: 阶段1 登录功能 - UI/接口/用户表/init_db/change_password）

阶段 1 登录部分已完成，可进入阶段 2（系统监控与进程管理）或先做 WSL/Linux 验证与提交。

---

## 9. 文档更新记录

| 日期 | 变更说明 |
|------|----------|
| 2026-03 | 初版；2026-03 统一为 Plan 格式（章节编号与命名） |
