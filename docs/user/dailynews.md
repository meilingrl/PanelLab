# DailyNews 新版：安装与日常使用

DailyNews 每天 23:20（北京时间）采集来源清单里的新闻，用大模型写成中文简报，发布到 `https://news.panellab.cn/<期号>/`（带密码保护），00:00 给你发一封提醒邮件，列出要闻前 5 条和链接。它作为[服务器 Agent](server-agent.md) 的两个普通作业运行，失败时由 Agent 发告警邮件。设计依据见[设计文档](../design/design-dailynews.md)，程序说明见 `apps/dailynews/README.md`。

下面所有 `sudo` 命令都由你自己在服务器的终端里运行。`<你的服务器>`、`<你的用户名>` 等尖括号内容换成你自己的值。

服务器在新加坡，网站不需要 ICP 备案。

## 开始之前

- 服务器上已经装好 PanelLab Agent，并在 Agent 页面配置好告警邮件（见[服务器 Agent](server-agent.md)）。DailyNews 的安装程序会检查 `panellab` 用户是否存在，没有就退出。
- 准备一个 DeepSeek API 密钥（在 DeepSeek 开放平台创建）。
- 准备一个能发信的邮箱和它的 SMTP 授权码（和 Agent 告警邮件用同一个即可）。
- 服务器上已有 nginx（旧版网站在用）。下面的步骤只新增一个站点配置，不改动旧的配置。

## 1. 域名解析和安全组

1. 打开阿里云「云解析 DNS」，选中 `panellab.cn`，添加一条记录：
   - 记录类型：`A`
   - 主机记录：`news`
   - 记录值：服务器的公网 IP
   - TTL：默认
2. 在 ECS 实例的「安全组」里确认入方向放行 TCP `80` 和 `443`（来源 `0.0.0.0/0`）。申请证书需要 80 端口，访问网站用 443。
3. 几分钟后在 Mac 上确认解析生效，输出应是服务器的公网 IP：

   ```bash
   dig +short news.panellab.cn
   ```

## 2. 打包、上传、安装

在 Mac 上，进入 PanelLab 仓库目录：

```bash
python3 apps/dailynews/make_bundle.py
scp apps/dailynews/dist/dailynews-0.1.0.tar.gz <你的用户名>@<你的服务器>:~/
```

（版本号以 `make_bundle.py` 打印的文件名为准。）

登录服务器后：

```bash
tar -xzf dailynews-0.1.0.tar.gz
sudo bash dailynews-0.1.0/install.sh
```

安装程序会：

- 把程序装到 `/srv/dailynews/app/`（属主 root，`panellab` 只能读）；
- 建网站目录 `/srv/dailynews/site/`（属主 `panellab`）和数据目录 `/var/lib/dailynews/`（属主 `panellab`，0750）；
- 第一次安装时把默认设置复制为 `/srv/dailynews/settings.json`（属主 `panellab`，0640）；**已有设置文件时不覆盖**；
- 以 `panellab` 身份校验设置，最后打印两个作业的字段（与第 7 步相同）。

它不会改动 nginx、旧的 `newsbot` 账户或 `/srv/news-briefing`。

网站目录是 0755、页面文件是 0644，nginx（`www-data`）本来就能读，不需要改组或权限。

以后升级：在 Mac 上重新打包、上传，再运行一次 `sudo bash dailynews-<新版本>/install.sh`。设置、数据和已发布的网页都保留。卸载程序用 `sudo bash dailynews-<版本>/install.sh --uninstall`，同样保留设置、数据和网站。

## 3. 安装证书工具和密码工具

已经装过的会自动跳过：

```bash
sudo apt install certbot python3-certbot-nginx apache2-utils
```

## 4. 设置网站密码

简报只给你自己看，用 HTTP 基本认证保护。把 `<用户名>` 换成你想用的登录名，按提示输入两次密码：

```bash
sudo htpasswd -c /etc/nginx/dailynews.htpasswd <用户名>
```

`-c` 会新建（覆盖）这个文件，以后只想加用户或改密码时去掉 `-c`。

## 5. nginx 站点配置

新建 `/etc/nginx/sites-available/dailynews`：

```bash
sudo nano /etc/nginx/sites-available/dailynews
```

内容：

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name news.panellab.cn;

    root /srv/dailynews/site;
    index index.html;
    charset utf-8;

    auth_basic "DailyNews";
    auth_basic_user_file /etc/nginx/dailynews.htpasswd;

    add_header X-Robots-Tag "noindex, nofollow" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "no-referrer" always;
    add_header Content-Security-Policy "frame-ancestors 'none'; base-uri 'none'; form-action 'none'; object-src 'none'" always;

    location / {
        try_files $uri $uri/ =404;
    }
}
```

启用并检查后重新加载：

```bash
sudo ln -s /etc/nginx/sites-available/dailynews /etc/nginx/sites-enabled/dailynews
sudo nginx -t && sudo systemctl reload nginx
```

`nginx -t` 报错时不要 reload，按提示改好再试。

## 6. 申请 HTTPS 证书

```bash
sudo certbot --nginx -d news.panellab.cn
```

certbot 会把证书写进上面的站点配置，并问你是否把 HTTP 跳转到 HTTPS，选跳转。证书会自动续期。完成之前不要在网页上输入密码（HTTP 下密码是明文传输的）。

## 7. 填设置，然后在 Agent 页面建两个作业

**先填提醒邮件的设置**（见第 8 步怎么改）：在 `smtp` 里填 `host`、`port`、`security`、`username`、`sender` 和 `recipients`。例如 QQ 邮箱：`"host": "smtp.qq.com"`、`"port": 465`、`"security": "ssl"`，`username` 和 `sender` 都是邮箱地址，`recipients` 写 `["你的邮箱"]`。阿里云封禁出站 25 端口，不要用 25。密码不写在设置文件里（写了会被拒绝），而是放在下面作业的秘密环境变量里。

然后在 PanelLab 侧边栏打开「服务器 Agent」，选中这台服务器，点「新建作业」两次：

| 字段 | 生成作业 | 提醒作业 |
| --- | --- | --- |
| 作业 ID | `dailynews-build` | `dailynews-notify` |
| 名称 | DailyNews 生成 | DailyNews 提醒 |
| 命令（每行一个参数） | `/usr/bin/python3`<br>`-m`<br>`dailynews`<br>`build` | `/usr/bin/python3`<br>`-m`<br>`dailynews`<br>`notify` |
| 工作目录 | `/srv/dailynews/app` | `/srv/dailynews/app` |
| 计划 | `20 23 * * *` | `0 0 * * *` |
| 时区 | `Asia/Shanghai` | `Asia/Shanghai` |
| 超时（秒） | 1800 | 120 |
| 失败重试次数 / 间隔（秒） | 2 / 600 | 1 / 300 |
| 补跑窗口（分钟） | 60 | 60 |
| 秘密环境变量 | `DAILYNEWS_LLM_API_KEY` = 你的 DeepSeek API 密钥 | `DAILYNEWS_SMTP_PASSWORD` = 邮箱的 SMTP 授权码 |

环境变量（明文）和环境变量文件都留空。告警保持默认（失败时通知、恢复时通知、附带输出）。

**第一次运行**：在 `dailynews-build` 的详情里点「立即运行」。第一次会采集最近 36 小时的新闻，通常几分钟内结束。看输出：

```
期号 2026-11-14
新文章 …
事件 …（后续 …，隐藏旧闻 …，过滤国内 …）
来源失败：无
大模型调用 … 次
```

然后打开 `https://news.panellab.cn`，输入第 4 步的用户名和密码，应能看到这一期。期号是“提醒那天”的日期：中午 12 点以后运行的生成作业，期号是第二天。

提醒作业只在当天那期已生成时才发信，所以白天手动运行它多半会以退出码 2 结束（“简报尚未生成，不发提醒”），这是正常的。第二天 00:00 它会按计划发出。

## 8. 修改设置

```bash
sudo -u panellab nano /srv/dailynews/settings.json
cd /srv/dailynews/app && sudo -u panellab /usr/bin/python3 -m dailynews check-settings
```

看到「设置有效：…」就好了，下一次运行就会用新设置。常改的项目：

- `sources`：来源清单。每个来源有 `id`、`name`、`url`、`kind`（`rss` / `atom` / `html`）、`category`、`type`（`agency` / `media` / `official` / `company`）和 `enabled`。只会采集这里列出的来源。改完可以运行 `sudo -u panellab /usr/bin/python3 -m dailynews check-sources`（同样在 `/srv/dailynews/app` 下）检查每个来源能否访问。
- `categories`：类别和权重（`weight` 越大越靠前）。
- `keywords_follow` / `keywords_block`：关注和屏蔽的关键词。
- `filter_china_domestic`：是否过滤主要关于中国国内事务的条目（涉及中国的国际新闻保留）。
- `top_count`、`per_category`：要闻条数、每个类别的条数；`notify_top`：提醒邮件列几条。
- `default_theme`：网页默认浅色 `light` 或深色 `dark`。
- `llm.max_calls_per_day`：每天大模型调用上限，默认 2000。

生成和提醒的时间不在设置文件里，在 Agent 页面改作业的计划。

## 9. 与旧版并行 7 天，再停旧版

新版装好后，旧版（`newsbot` 的定时器，或你已迁进 Agent 的旧作业）先照常运行。7 天里每天对照看：

- Agent 页面两个作业是否每天都成功（绿色），有没有收到失败告警；
- 提醒邮件是否 00:00 前后到达，链接能否打开；
- 要闻是否覆盖了旧版里你在意的新闻，摘要是否忠于原文（展开看原标题和来源链接）；
- 同一件事是否被合并、昨天的旧闻是否被隐藏、国内新闻过滤是否合理；
- 生成作业输出里的「来源失败」和「大模型调用 … 次」是否正常。

满意之后停掉旧版：

- 旧版仍是 `newsbot` 的 systemd 用户定时器时，按[服务器 Agent 指南的「示例：把旧 DailyNews 迁到 Agent」](server-agent.md#示例把旧-dailynews-迁到-agent)第 1 步找到实际的定时器名字，再用第 6 步的 `systemctl --user disable --now …` 命令逐个停掉；
- 旧版已经迁成 Agent 作业时，在 Agent 页面「暂停」（确认不再需要后再「删除」）那两个旧作业。

旧的代码、配置和定时器文件不用删除；想回退时把 `disable --now` 换成 `enable --now`，或在 Agent 页面「继续运行」旧作业。

## 10. 出问题时

作业的退出码和输出都在 Agent 页面的运行历史里。

| 现象 | 含义与处理 |
| --- | --- |
| 退出码 2（生成作业） | 设置不合法（输出写明哪一项，按第 8 步改好），或缺少 `DAILYNEWS_LLM_API_KEY`（编辑作业补上秘密环境变量）。 |
| 退出码 2（提醒作业） | 当天那期没有生成，所以不发提醒：先看前一晚生成作业的运行记录。也可能是设置里 `smtp.host`、`sender`、`recipients` 没填，或缺少 `DAILYNEWS_SMTP_PASSWORD`，输出会写明。 |
| 退出码 3（生成作业） | 部分降级，网页**仍然发布**了：超出了当天大模型调用上限，或一半以上来源失败。输出里的「来源失败：…」列出失败的来源和原因；偶尔一两天可以不管，持续失败就用 `check-sources` 检查，换掉或停用该来源。 |
| 退出码 1 | 其他失败。生成作业的输出形如「构建失败（阶段：…）：…」，写明卡在哪个阶段；提醒作业是「提醒邮件发送失败：…」，多半是 SMTP 主机、端口、安全方式或授权码不对。 |
| 条目标着「未生成摘要」 | 大模型没能为这一条写摘要（超出当天调用上限，或接口多次出错），这里显示的是原文的片段。看生成作业输出里有没有「已超出今日大模型调用上限」；需要时调高 `llm.max_calls_per_day`，或检查 DeepSeek 账户余额和密钥。 |
| 条目标着「仅据标题和简介」 | 原文网页没能抓到（付费墙、拒绝访问等），摘要只依据来源订阅里的标题和简介，信息可能不完整。点来源链接看原文。 |
| 打开网站提示 401 / 一直要密码 | 用户名或密码不对，按第 4 步用 `htpasswd`（不带 `-c`）重设。 |
| 打开网站 403 / 404 | 还没有任何一期：先在 Agent 页面运行一次生成作业。仍然不行就检查第 5 步的 `root` 路径，以及 `sudo nginx -t`。 |
