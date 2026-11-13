# DailyNews

每日要闻简报：只用 Python 标准库（兼容 3.9），作为 PanelLab Agent 的普通作业运行。
设计见 `docs/design/design-dailynews.md`。

## 本地运行与测试

```
cd apps/dailynews
python -m pytest -q
python -m dailynews --settings default_settings.json check-settings
```

## 设置

`default_settings.json` 是默认设置（含来源清单），安装时若没有 `/srv/dailynews/settings.json` 则复制。
秘密只来自环境变量 `DAILYNEWS_LLM_API_KEY`、`DAILYNEWS_SMTP_PASSWORD`，设置文件里出现 `api_key` / `password` 会被拒绝。
SMTP 的 host 留空时不校验 SMTP 其它字段；填写 host 后要求有发件人和至少一个收件人。
来源清单于 2026-11-13 在 Mac 上用 `check-sources` 联网核实，启用的 25 个来源全部可用。

来源清单里没有 Reuters、AP、AFP：它们没有公开的官方订阅地址，不虚构地址；`agency`（通讯社）仍是合法的来源类型，以后有官方订阅时可直接加入。

核实时调整的来源（2026-11-13）：
- NHK World：原订阅地址先超时、后返回 404，NHK World 新闻页改为脚本渲染，找不到官方 RSS；已删除，换成同为亚洲大型国际媒体的 CNA（新加坡 Mediacorp）国际频道官方订阅。
- IMF：新闻订阅地址返回 404/429，官网改版后找不到新闻发布的官方 RSS（媒体中心的订阅只有视频简报，且一个月未更新）；已删除，换成英格兰银行（Bank of England）官方新闻订阅。
- 美联储、欧洲央行、Google AI Blog：核实可用，已启用。
- Google DeepMind：保留但停用。官方订阅有时不经请求就返回 gzip 压缩内容，当前的 `http.get` 不解压，解析报「不支持的编码」；`http.py` 支持解压后再启用。
- Meta AI：保留但停用。它没有官方订阅，`html` 方式从博客列表页取到的多是导航链接和「Learn More」之类的标题。
- Anthropic 同样没有官方订阅，按 `html` 方式抓取新闻列表页，能取到真实文章，但也会带上少量栏目链接（如 Announcements、Features），保持启用。

## 大模型费用上限

只有每日调用次数上限（`llm.max_calls_per_day`，默认 2,000 次），没有单独的 token 上限；单日费用上限约为“调用次数 × 每次的 max_tokens（各阶段 2,000–4,000）”加上输入长度。

## 安装包与服务器安装

```
python3 apps/dailynews/make_bundle.py        # 输出 apps/dailynews/dist/dailynews-<版本>.tar.gz
```

安装包是确定性的（固定顺序、mtime 0、属主 root），不含 tests 和缓存。服务器上解包后运行 `sudo bash install.sh`（需要先装好 PanelLab Agent），`--uninstall` 只删程序，保留设置、数据和网站。完整步骤（域名、nginx、证书、Agent 作业）见 `docs/user/dailynews.md`。

在 systemd 容器里验证安装和两个作业：`python3 scripts/verify_agent_lab.py --dailynews`。

## 提醒邮件端口

`smtp.port` 留 0 时，`notify` 按 security 取默认端口：ssl 465、starttls 587、none 25。这与 Agent 告警邮件的默认值（ssl 465，其余 25）有意不同：云服务器常封锁出站 25 端口，starttls 应走 587。
