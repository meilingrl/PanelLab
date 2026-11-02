# Safe Site Changes Implementation Plan

> **For agentic workers:** Use subagent-driven-development. 主控统筹，各任务小步提交；不创建子代理。

**Goal:** 完成交互可审查、失败可恢复、有证据的本机站点变更。
**Architecture:** 签名预览与单入口执行；共享主机写锁；独立追加审计表；Vue 预览确认工作流。
**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / Vue 3 / Vite。
**Spec:** [design-safe-site-changes.md](design-safe-site-changes.md)

## Global Constraints

- 不接触真实服务器/系统 Nginx/真实数据库；只用本项目隔离测试资产。
- 所有写改动留在 feat/production-reliability；只提交自己拥有文件，小量多次提交。
- 配置前后不能宣称业务健康；模拟不等于应用成功；恢复失败必须可见。
- 遵守 /Users/meilingluo/AGENTS.md；不推送、合并、部署或清理其他资产。
- 先写可失败的行为测试，再实现；报告实际命令和结果，不伪造证据。

## Task 1: 生产入口防护

Files: backend/config.py、backend/routers/auth.py、backend/routers/db_admin.py、backend/routers/sites.py、backend/.env.example、backend/tests/test_production_guards.py、backend/tests/conftest.py、backend/tests/test_api.py。

Read spec section 8. 独立实现生产与开发安全开关：`enable_demo_auth: bool=False`；生产无条件关闭注册和全部第三方/SMS 路由；开发注册可保留，开发 mock 仅开关打开才可访问。`operator_usernames: str='admin'` 逗号分隔，`require_operator` 生产校验当前用户，开发保留旧行为。站点所有读写及DB管理均用此依赖，防止读取全局资源越权。`GET /api/auth/capabilities` 返回 password/register/sms/wechat/qq/demo 布尔项；关闭服务返回 403/503，不返回token。所有OAuth入口都检查，包括GET回调、轮询与POST mock。生产配置用 model_validator 拒绝 secret 少于32字符或示例值、初始密码少于12字符或默认值、skip_apply=True、enable_demo_auth=True；app_env 限定 development/test/production，避免误拼绕过。第三方密码占位用随机不可猜值，不保留公共 no-password-placeholder。

- [ ] 写 tests/test_production_guards.py，构造 production 安全配置，验证坏配置拒绝、所有关闭路由无token、普通账号共享资源403、管理员允许。
- [ ] `/Users/meilingluo/Developer/Projects/PanelLab/.venv311/bin/python -m pytest tests/test_production_guards.py -q`，记录RED。
- [ ] 最小实现依赖和配置；旧test_api需在测试环境显式打开 demo，不能改变生产默认。
- [ ] 同命令及 tests/test_api.py 验证GREEN；分两次左右提交 guard 与认证路径修复。
- [ ] 报告 API capabilities 精确结构供前端使用。

## Task 2: 变更引擎与API

Files: backend/services/nginx_manager.py；新增 services/site_changes.py、models/site_change.py、schemas/site_changes.py、routers/site_changes.py；修改 schemas/sites.py、routers/sites.py、main.py、models/__init__.py；新增 tests/test_site_changes.py、test_nginx_changes.py；调整旧站点行为测试。

完整实现设计的1–7项及API契约；使用 Task1 require_operator。预览token用现有JWT库带独立purpose和5分钟expiry，执行验证purpose/user，再严格校验payload。token jti唯一约束审计，原子claim防重放。审计pending提交后执行；最后提交出错返回人工核查错误不宣称成功。快照hash包括站点所有影响配置的字段；目录指纹考虑*.conf字节与缺失；不读取目录外符号链接。create不提前落站点，使用随机安全文件名或预定安全ID，且处理唯一约束冲突。共享全局锁覆盖重查/应用/DB提交；preview无需落盘。真实变更成功后才更新数据库，skip_apply结果site.status='simulated'。校验字段可复用新services/site_validation.py，以schema和manager两层入口一致约束。

- [ ] 写行为测试，先验证preview无写入、无token旧写428、过期/篡改/异用户拒绝、两个预览竞争后第二个409、外部conf改变409、注入422、模拟非active。
- [ ] 运行新测试确认RED，记录输出。
- [ ] 分别实现严格渲染/文件恢复、审计模型、预览执行API，按完整可检验单元2–4次提交。
- [ ] 用tmp_path和受控脚本验证实际原子写入、test失败原文件恢复、reload失败恢复并再次reload、恢复失败显式、超时与并发、历史按用户/删除后保留；不只断言mock调用。
- [ ] 全后端测试GREEN，并将最终精确JSON和状态码写报告给前端。

## Task 3: 预览确认、历史与登录能力UI

Files: frontend/src/views/dashboard/Sites.vue，可提取 frontend/src/components/SiteChangeReview.vue；frontend/src/views/Login.vue、Register.vue；frontend/package.json/package-lock.json；frontend/tests/；.github/workflows/ci.yml 前端测试步骤。

Read design and backend schemas. Sites GET继续列数据，所有create/update/apply/delete走新preview/execute；表单submit只预览，必须独立按钮确认。预览显示本机作用域、模式、diff、到期与警告；表单改动或另一操作会清预览，异步旧响应丢弃；执行后刷新列表+历史。pending/recovery_failed明确需核查，simulated非绿色成功。历史可展开before/after/步骤，支持加载下一页且空状态/错误不误导。保留页面整体样式、键盘标签、移动端布局，避免HTML渲染服务器数据。auth capabilities隐藏关闭入口，直接Register路径也显示关闭状态。

- [ ] 用浏览器或Vitest组件行为测试编写可失败场景：点击保存只preview；修改字段令旧token无效；确认才execute；重复点击不能发多个execute；错误不丢历史；删除/应用同预览流程。选择少量真正交互测试，不做源码字符串断言。
- [ ] 观察RED，按接口实现UI。测试依赖锁定；顺带在既有semver范围内修复npm audit现有告警，不做破坏升级。
- [ ] npm test及npm run build通过；更新CI运行前端test。
- [ ] 2次左右提交功能与测试依赖；报告浏览器操作和截图路径（若有）。

## Task 4: 集成证据与交付说明（主控）

- [ ] 汇总独立代码审查，修复重要发现，运行最终后端/前端检查。
- [ ] 尝试隔离真实Nginx验证；如无环境，明确记录局限。
- [ ] 更新 README、docs/README、用户手册和部署说明仅相关部分；本Plan记录验证结果与剩余风险。
- [ ] 保留干净worktree和提交链，主仓库main不变；本地交付，不推送/合并/部署。
