# Plan：可审查、可恢复的站点变更

日期：2026-11-02。用户授权主控自主选题、实现和小步提交；本 Plan 是主控选定的首个垂直切片，尚不代表生产验收。

## 目标与选择

PanelLab 面向管理少量 Linux 主机的个人和小团队。本轮聚焦一个高频高风险任务：修改本机 Nginx 站点时，知道将改什么、避免覆盖别人的修改、失败后知道恢复结果。

比较过三个方向：

| 方向 | 价值 | 本轮选择 |
| --- | --- | --- |
| 站点变更预览、冲突检测、恢复证据 | 利用已有网站工作流，验收边界清楚 | 首先实现 |
| 统一 SSH 服务器库与主机信任 | 解决终端与监控身份割裂 | 后续独立设计 |
| 磁盘/服务故障的只读诊断快照 | 将指标变成排障步骤 | 先补可靠采集后再做 |

差异化假设是“小规模自托管运维的变更可解释性”，不是声称首创回滚。1Panel 已有广泛网站设置与备份，Coolify 已有部署记录与恢复能力：
- https://1panel.pro/docs/v2/user_manual/websites/website_config_basic/
- https://1panel.pro/docs/v2/user_manual/websites/website_backup/
- https://coolify.io/docs/applications/deployments/overview

## 范围与验收

1. 创建、修改、重新应用、删除均先预览；预览不改数据库/配置、不运行 Nginx。显示实际磁盘旧配置与候选新配置 unified diff，注明作用于面板本机和是否模拟。
2. 预览凭证有效期 5 分钟，绑定用户、操作、目标、候选参数、当前数据库状态和受管目录配置指纹。确认时在全局进程/文件锁内重新核对；篡改、过期、配置漂移、重复执行被拒绝，不覆盖新状态。
3. 严格约束 domain/root/proxy，防止 Nginx 指令注入；配置文件名限制为受管 basename；拒绝符号链接/目录逃逸。外部用户不能通过 API 自选任意配置文件。
4. 同目录临时文件原子替换，执行 nginx -t 后 reload。校验失败恢复原文件；reload 失败恢复文件并再次 test/reload 恢复运行态。恢复失败单独显式标注，不吞异常、不宣称已恢复。超时有界。
5. 先记录 pending 变更，再接触运行态；成功后更新站点数据。失败保留原站点数据并记录结果。数据库最终提交失败不得声称成功，保留可发现的 pending/需人工核查证据。跨数据库、文件与 Nginx 不承诺分布式原子性。
6. 变更历史按用户隔离，包含操作、站点名称、时间、配置前后、结果及 test/reload/restore 步骤；站点删除后记录仍可读。最多每页 50 项，有明确排序。默认不记录命令 stdout/stderr 原文和敏感参数；错误消息安全、可理解。
7. NGINX_SKIP_APPLY 只允许非生产环境。模拟结果明确标注，绝不伪装 active。预览不证明 nginx 语法已通过；reload 成功也不证明 HTTP 业务可用。
8. 生产模式拒绝默认/过短密钥、弱初始密码；关闭自助注册、SMS/微信/QQ 登录直到其完整安全流程另行验证。共享主机的站点与 MySQL 管理仅允许配置的运维账号；既有普通账号仍可管理自己的 SSH 服务器。开发模拟登录必须明确开启。
9. 前端所有动作走同一预览确认流程；表单变更废弃旧预览，异步竞态不能把旧预览套到新表单；操作中禁用重复提交；失败仍能查看历史。展示生产登录能力，隐藏关闭的入口。

## 架构与接口

保留 SiteConfig 表结构，新增 SiteChange 审计表，由现有 create_all 创建。schema migration 不重写既有表。

新增 `/api/site-changes` 路由：
- `POST /preview`：`{operation: create|update|apply|delete, site_id?: int, config?: SiteBase}`。返回 `{token, expires_at, operation, site_id, site_name, before, after, diff, simulated, warnings}`。
- `POST /execute`：`{token}`。成功返回 `{change, site: SiteItem|null}`；失败返回明确 HTTP 错误和可读取的历史记录。token 包含经签名的候选参数，execute 不接受独立配置覆盖。
- `GET ""`：`{items: SiteChangeItem[]}`，可用 `before_id` 游标、`limit` 1..50。item 至少含 id/site_id/site_name/operation/status/created_at/before/after/steps/message/simulated。
- 原 `/api/sites` 写接口停止直接生效，授权检查后返回 428 引导预览；GET 保留。避免旧路径绕过保护。

状态至少区分 `pending`、`applied`、`simulated`、`failed_restored`、`recovery_failed`。步骤包含 name/status/message，错误消息不含未过滤 Nginx 输出。生产锁使用标准库 fcntl；开发 Windows 可给出明确不支持实际应用的错误，模拟仍可用。所有 PanelLab 写入共享同一锁；不承诺阻止面板外管理员在执行中写盘。

## 非目标及限制

不做集群、远程 Nginx、自动 TLS、任意 Shell、AI 执行、HTTP 探测、自助多租户或历史一键回滚。自动恢复只针对本次失败；历史内容帮助人工恢复，未来可将历史恢复也建模为新变更。未解决的 SSH AutoAddPolicy、终端查询参数 token、生产迁移、限流等风险需在交付记录明确保留，不宣称整站生产就绪。

## 验证

后端：签名/过期/重放/跨用户、DB与磁盘漂移、配置注入/路径边界、并发锁、真实临时文件和受控子进程故障、恢复失败、模拟状态、权限与生产配置测试。
前端：预览必须先于执行、旧预览作废、错误显示、历史状态、删除/应用同路径；浏览器验证响应式布局。
集成：尝试本地隔离 Nginx 非特权端口实际 test/reload，不操作系统 Nginx、生产主机或用户数据；无法运行时明确未验证。

## 工作记录

基线 `f9df941`；分支 `feat/production-reliability`，项目专属 worktree。原始前端 build 通过，依赖审计存在 8 项告警待处理；后端基线核验中。
