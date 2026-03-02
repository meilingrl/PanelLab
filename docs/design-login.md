# 登录功能：需求与设计（阶段 1）

本文档记录阶段 1「用户体系与仪表盘」中登录部分的约定，便于后续维护与扩展。

---

## 1. 需求范围（本阶段）

| 项目 | 说明 |
|------|------|
| 登录 | 用户名 + 密码，校验通过后进入仪表盘 |
| 登出 | 清除登录态，跳转登录页 |
| 登录态校验 | 未登录访问需鉴权页时重定向到登录页 |
| 错误提示 | 用户名或密码错误时统一提示，不区分具体原因 |

本阶段不实现：注册、找回密码、多用户角色、第三方登录。

---

## 2. 接口约定

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/auth/login | 请求体 `{ username, password }`，成功返回 `{ token, user: { username } }`，失败 401 |
| POST | /api/auth/logout | 前端删 token 为主，后端返回 `{ message: "ok" }` |
| GET | /api/auth/me | Header `Authorization: Bearer <token>`，返回当前用户 `{ username }`，无效 401 |

认证方式：JWT，前端将 token 存 localStorage（键 `panel_token`），请求时带在 Authorization 头。

---

## 3. 数据库

- 表 `users`：id, username（唯一）, password_hash, created_at, updated_at。
- 初始管理员：通过 `python -m init_db` 创建（仅当无用户时），用户名 admin，密码来自 `.env` 的 `INIT_ADMIN_PASSWORD`。
- 修改密码：`python change_password.py admin 新密码`（见 README）。

---

## 4. 前端要点

- 路由：`/login` 公开，`/` 需登录；未登录访问 `/` 重定向到 `/login?redirect=/`。
- 登录页：居中白卡、左右分栏（Logo+品牌 / 表单）、线条背景、上浮标签、明暗主题切换。
- 仪表盘：占位页，含主题切换与退出按钮。

---

## 5. 阶段 1 完成清单

- [x] 需求明确与详细设计
- [x] 登录页 UI（居中卡片、线条背景、上浮标签、主题切换、Logo）
- [x] 仪表盘占位与路由鉴权
- [x] 后端接口：login / logout / me，JWT
- [x] 用户表与 init_db、change_password
- [x] Windows 本地测试通过（登录 / 登出 / 错误密码 / 主题）
- [ ] WSL 或虚拟机 Linux 环境测试（可选，按 [testing-steps.md](testing-steps.md) 第六节）
- [ ] Git 提交（建议提交信息：feat: 阶段1 登录功能 - UI/接口/用户表/init_db/change_password）

阶段 1 登录部分已完成，可进入阶段 2（系统监控与进程管理）或先做 WSL/Linux 验证与提交。
