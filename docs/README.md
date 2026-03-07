# PanelLab 文档索引

本目录存放需求、设计与实施说明。**局部项目规划**统一采用「Plan」格式（需求与设计合一），便于迭代与协作。

---

## 文档结构

| 类型 | 文件 | 说明 |
|------|------|------|
| **总需求** | [requirements.md](requirements.md) | 产品目标、用户场景、功能范围、阶段与优先级 |
| **Plan / 设计** | `design-*.md`、`plan-*.md` | 各阶段或各功能模块的**需求与设计**，格式统一（见下） |
| **模板** | [_template-plan.md](_template-plan.md) | 新建局部规划时复制此模板，按节填写 |
| **环境与测试** | [testing-steps.md](testing-steps.md)、[check-mysql.md](check-mysql.md)、[linux-testing-wsl-vm.md](linux-testing-wsl-vm.md) | 环境准备、MySQL 校验、WSL/虚拟机测试 |
| **协作与部署** | [setup-github.md](setup-github.md)、[deploy.md](deploy.md) | GitHub 仓库关联与推送；Docker/生产部署步骤 |

---

## Plan 格式说明（需求与设计合一）

所有**局部项目规划**（新功能、新阶段、子模块）均采用同一套结构，便于：

- 需求与实现要点集中在一份文档
- 与 AI/协作方对齐范围与接口
- 用「本阶段完成清单」跟踪进度

### 标准章节（按需保留或略写）

1. **目标与范围** — 本 Plan 要解决什么、包含/不包含哪些
2. **功能需求** — 必须/可选功能、交互与体验（可合并到 1）
3. **接口约定** — API 路径、请求/响应、鉴权（无则写「无」或略）
4. **数据与存储** — 表结构、配置存储、加密等（无则略）
5. **后端实现要点** — 路由、依赖、错误与安全
6. **前端实现要点** — 页面、路由、状态、样式
7. **与后续阶段衔接** — 与其它 Plan 或总阶段的依赖/扩展点
8. **本阶段完成清单** — 可勾选的任务列表，用于验收与提交
9. **文档更新记录** — 变更日期与说明

新建 Plan 时：复制 [\_template-plan.md](_template-plan.md)，重命名为 `plan-<模块名>.md` 或延续现有 `design-<模块名>.md`，按节填写即可。若某模块需单独章节（如「前置条件」「配置方式」），可插入在 1、2 节之后，后续章节顺延编号。

---

## 当前 Plan / 设计文档

| 文档 | 对应阶段/模块 |
|------|----------------|
| [design-login.md](design-login.md) | 阶段 1：登录、登出、注册、修改密码 |
| [design-dashboard.md](design-dashboard.md) | 阶段 1：仪表盘布局与多界面骨架 |
| [design-monitor.md](design-monitor.md) | 阶段 2：本机系统监控（CPU/内存/磁盘/网络） |
| [design-monitor-remote.md](design-monitor-remote.md) | 阶段 2 扩展：远程 Linux 监控（SSH + 采集脚本） |
| [design-sites-proxy.md](design-sites-proxy.md) | 阶段 3：网站与反向代理（站点 CRUD、Nginx 配置与生效） |
| [plan-project-progress.md](plan-project-progress.md) | 项目整体：进度评估、里程碑重排、周期工作安排 |
| [core-technology-overview.md](core-technology-overview.md) | 项目整体：各主要功能的核心技术讲解 |

阶段 3（网站与反向代理）、阶段 4（数据库管理）等的新规划，建议直接使用 `_template-plan.md` 生成新的 `plan-*.md` 或 `design-*.md`。
