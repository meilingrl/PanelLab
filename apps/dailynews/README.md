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
来源地址是按各机构公开订阅地址填写的，尚未联网核实（Task 11 核实）。

来源清单里没有 Reuters、AP、AFP：它们没有公开的官方订阅地址，不虚构地址；`agency`（通讯社）仍是合法的来源类型，以后有官方订阅时可直接加入。
地址不确定的来源（NHK World、ECB、美联储、IMF、Google DeepMind、Google AI）先设为 `"enabled": false`，由 Task 11 的 check-sources 核实后再启用。

## 提醒邮件端口

`smtp.port` 留 0 时，`notify` 按 security 取默认端口：ssl 465、starttls 587、none 25。这与 Agent 告警邮件的默认值（ssl 465，其余 25）有意不同：云服务器常封锁出站 25 端口，starttls 应走 587。
