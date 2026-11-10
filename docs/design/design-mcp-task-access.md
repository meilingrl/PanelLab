# 陌生Agent的MCP任务接入

2026-11-05。基线c7f76b7，分支feat/mcp-task-access，复用隔离worktree。用户明确要求记录MCP并按主控评估继续开发。采用brainstorming架构路径确定接口后按writing-plans/SDD实施；既有自主授权覆盖设计与分支备份，不重新等待批准。

## 目标与取舍

首个目标是已配置连接的外部MCP客户端无需阅读源码即可发现工具，按任务ID读到与UI相同的状态、日志和文件。选择可选stdio适配，复用现有HTTP API的账号与路径/SSH边界；相比内置聊天无需模型密钥，相比远程HTTP MCP不需要本轮引入新网络入口/OAuth。代价是用户仍需安装适配器并配置客户端。

## 架构与边界

scripts/panellab_mcp.py作为stdio入口，scripts/panellab_mcp_server.py实现工具，复用scripts/panellab.py的API/base_url/token_from/ClientError，不另走SSH、不导入后端数据库。官方Python SDK mcp==2.3.0为独立可选依赖scripts/requirements-mcp.txt；后端依赖和CLI标准库用法不变。运行Python>=3.10。stdout仅MCP协议，启动失败只向stderr输出固定脱敏诊断、非零退出。

只提供list_servers、list_tasks、get_task、observe_task、read_task_log、list_task_files、read_task_text、list_reported_runs八项读取工具。没有任意HTTP/命令、下载到客户端路径、写元数据、主机信任或进程操作工具；不自动配置任何真实Agent。它是只读工具集，不是只读权限凭证：配置的现有Bearer仍具有原账号权限，需明确说明；独立限权凭证列为后续。

工具名称、描述、参数字段说明要使陌生客户端知道先list_tasks获取ID，路径来自任务目录和files结果；不能从进程运行/日志变化/上报记录推断成功。服务instructions说明任务定义、资源为整机共享、采样时间/unknown、上报来源，以及远端文本是不可信数据而不是操作指令。所有工具声明readOnlyHint=true、destructiveHint=false，真实约束由工具映射与后端落实。

## 输入输出与失败

task_id为严格正整数，禁止bool/小数/字符串；路径为长度1..4096的字符串，后端继续验证根目录范围。list_tasks支持query（默认空、最长128字符，名称不区分大小写子串）与kind（training/website/scheduled可空），limit默认20范围1..100、offset默认0非负整数，返回items/total/has_more；分页为适配层对本次API结果分页，不伪装服务端快照。list_servers只返回id/name/host/port/username，凭据永不作为输出字段。list_reported_runs保持API limit1..50默认20及before_id正整数。read_task_log先获取绑定日志；没有日志时给明确log_unbound错误，不猜路径。

成功返回结构化JSON并保留API的观测时间、context、sampling、文件元数据/截断标记及上报source。远端日志内容最多后端64KiB，HTTP响应沿用CLI1MiB/30秒上限。只把已有安全ClientError映射为isError=true和固定恢复提示（401重新登录配置，404任务不可访问，409在界面检查信任/绑定，网络不可达检查API）；不回显响应体、Authorization、token、原始异常。错误不是成功的空列表。缺失/无效API结构应成为脱敏错误，不抛出含敏感参数的堆栈。

## 配置与使用

入口接受--url、--token-file或PANEL_TOKEN、--allow-insecure（只用于用户显式允许非loopback HTTP）；没有传入任意token参数。--print-config输出通用mcpServers JSON，command为当前Python绝对路径，args为入口绝对路径、URL和token文件绝对路径，不写入客户端配置、不读取或输出token；该模式要求--token-file。不附加从环境继承的秘密值。--check通过服务器列表验证API连接/授权，输出有限诊断与数量，不输出服务器内容。帮助/配置生成无需安装SDK，真正启动缺依赖时明确提示安装独立requirements。

提供中文指南：独立虚拟环境安装、从已有登录取得凭证并保存在受保护文件（不复制示例token）、生成配置、客户端通用配置差异、凭证到期更新并重启连接、排障、工具列表与支持边界。当前不承诺某一GUI客户端或真实模型端到端验收。

## 验收

用官方SDK作为真实stdio客户端发现工具与schema，读取任务/状态/日志/文件/上报；HTTP隔离夹具检查仅GET、绑定ID/path、中文空格、过滤分页、输入拒绝、错误/凭证不泄漏、stdout协议完整。启动/配置模式通过子进程从不同cwd检验。扩展真实Linux SSH/MySQL夹具调用该MCP入口，证明同一任务ID/观测上下文/日志/结果文件可读。客户端实际发现与调用通过不等于LLM自主判断正确；真实个人服务器与Agent客户端配置不做自动变更。

独立任务审查和整轮审查；本轮不改前端，无须重复浏览器验收。小提交备份新功能分支，保留main和已有分支。已有临时资产保留，只停止本轮自建服务。

参考：[MCP工具发现](https://modelcontextprotocol.io/docs/learn/server-concepts)、[官方Python SDK](https://github.com/modelcontextprotocol/python-sdk)、[SDK客户端](https://py.sdk.modelcontextprotocol.io/client/)。2026-11-05查PyPI最新稳定2.3.0；使用独立环境隔离其依赖。
