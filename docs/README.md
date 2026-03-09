# PanelLab 文档索引

本目录存放需求、设计与实施说明。**局部项目规划**统一采用「Plan」格式（需求与设计合一），便于迭代与协作。

---

## 目录结构（向下细分）

```
docs/
├── README.md              # 本索引
├── design/                # 各阶段/模块需求与设计（Plan）
│   ├── _template-plan.md  # 新建 Plan 时复制的模板
│   ├── design-login.md
│   ├── design-dashboard.md
│   ├── design-monitor.md
│   ├── design-monitor-remote.md
│   ├── design-monitor-ui-enhancements.md
│   ├── design-sites-proxy.md
│   ├── design-database-tasks.md
│   └── design-terminal.md      # 网页端 SSH 终端
├── user/                  # 用户向文档
│   └── user-manual.md     # 用户使用说明书
├── project/               # 项目总览与需求
│   ├── requirements.md    # 产品目标、阶段与优先级
│   ├── plan-project-progress.md  # 进度、里程碑、周期安排
│   └── core-technology-overview.md  # 核心技术讲解
├── environment/           # 环境、测试、依赖
│   ├── testing-steps.md   # 完整测试与验收步骤
│   ├── check-mysql.md     # MySQL 环境校验
│   └── linux-testing-wsl-vm.md  # WSL2/虚拟机 Linux 测试
├── deploy/                # 部署与协作
│   ├── deploy.md          # Docker/传统部署说明
│   └── setup-github.md    # GitHub 仓库关联与推送
└── guides/                # 教程与工具
    └── dbeaver-tutorial.md  # DBeaver 使用教程
```

---

## 文档速查

| 类型 | 路径 | 说明 |
|------|------|------|
| **用户** | [user/user-manual.md](user/user-manual.md) | **用户使用说明书**：登录、各功能操作与常见问题 |
| **总需求** | [project/requirements.md](project/requirements.md) | 产品目标、用户场景、功能范围、阶段与优先级 |
| **设计/Plan** | [design/](design/) | 各阶段需求与设计（见下表） |
| **模板** | [design/_template-plan.md](design/_template-plan.md) | 新建局部规划时复制此模板 |
| **进度** | [project/plan-project-progress.md](project/plan-project-progress.md) | 进度评估、里程碑、周期工作 |
| **技术概览** | [project/core-technology-overview.md](project/core-technology-overview.md) | 各主要功能核心技术讲解 |
| **环境与测试** | [environment/testing-steps.md](environment/testing-steps.md)、[environment/check-mysql.md](environment/check-mysql.md)、[environment/linux-testing-wsl-vm.md](environment/linux-testing-wsl-vm.md) | 环境准备、MySQL 校验、WSL/虚拟机 |
| **部署** | [deploy/deploy.md](deploy/deploy.md)、[deploy/setup-github.md](deploy/setup-github.md) | Docker/生产部署；GitHub 协作 |
| **教程** | [guides/dbeaver-tutorial.md](guides/dbeaver-tutorial.md) | DBeaver 使用教程 |

### 当前 Plan / 设计文档（design/）

| 文档 | 对应阶段/模块 |
|------|----------------|
| [design-login.md](design/design-login.md) | 阶段 1：登录、登出、注册、修改密码 |
| [design-dashboard.md](design/design-dashboard.md) | 阶段 1：仪表盘布局与多界面骨架 |
| [design-monitor.md](design/design-monitor.md) | 阶段 2：本机系统监控（CPU/内存/磁盘/网络） |
| [design-monitor-remote.md](design/design-monitor-remote.md) | 阶段 2 扩展：远程 Linux 监控 |
| [design-monitor-ui-enhancements.md](design/design-monitor-ui-enhancements.md) | 阶段 2：圆环、流量图、远程安装 psutil |
| [design-sites-proxy.md](design/design-sites-proxy.md) | 阶段 3：网站与反向代理 |
| [design-database-tasks.md](design/design-database-tasks.md) | 阶段 4：数据库与计划任务 |
| [design-terminal.md](design/design-terminal.md) | 网页端 SSH 终端（连接/断开、数据流、故障排查） |

阶段 5 或其它新增模块的规划，建议复制 [design/_template-plan.md](design/_template-plan.md) 生成新的 `design-*.md` 或 `plan-*.md`，放在 `design/` 下。

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

新建 Plan 时：复制 [design/_template-plan.md](design/_template-plan.md)，重命名为 `design-<模块名>.md` 或 `plan-<模块名>.md`，放在 `design/` 目录下，按节填写即可。
