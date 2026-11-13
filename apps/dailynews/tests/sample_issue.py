"""生成一份样例站点，供看版式用（不是测试，pytest 不收集）。

用法：python tests/sample_issue.py <输出目录>
内容是编写的占位新闻，不是真实报道。
"""
import os
import sys
import tempfile
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from dailynews.render import HiddenItem, render_index, render_issue  # noqa: E402
from dailynews.settings import load_settings  # noqa: E402
from dailynews.store import Store  # noqa: E402
from dailynews.summarize import EventOut, SourceLink  # noqa: E402

S = SourceLink
BBC = S("BBC News", "https://www.bbc.co.uk/news/world-0000001")
GD = S("The Guardian", "https://www.theguardian.com/world/2026/nov/13/sample")
DW = S("Deutsche Welle", "https://www.dw.com/en/sample/a-0000001")
NPR = S("NPR", "https://www.npr.org/2026/11/13/sample")
AJ = S("Al Jazeera English", "https://www.aljazeera.com/news/2026/11/13/sample")
UN = S("UN News", "https://news.un.org/en/story/2026/11/0000001")
VERGE = S("The Verge", "https://www.theverge.com/2026/11/13/sample")
ARS = S("Ars Technica", "https://arstechnica.com/sample/2026/11/")
OPENAI = S("OpenAI", "https://openai.com/index/sample/")
HF = S("Hugging Face", "https://huggingface.co/blog/sample")
BBCB = S("BBC Business", "https://www.bbc.co.uk/news/business-0000001")


def ev(title, orig, cat, summary, detail, sources, section="category", status="new", change=None,
       prev=None, degraded=False, basis="body"):
    return EventOut(title_zh=title, title_orig=orig, category=cat, status=status, summary_zh=summary,
                    detail_zh=detail, change_zh=change, sources=sources, prev_issue=prev, degraded=degraded,
                    basis=basis, section=section, score=5.0, article_ids=(1,), prev_event_id=None)


EVENTS = [
    ev("欧盟成员国就 2027 年预算框架达成初步一致",
       "EU states reach preliminary deal on 2027 budget framework", "politics",
       "经过两天谈判，欧盟各成员国就 2027 年度预算框架达成初步一致。协议提高了防务与能源转型方面的支出比例，仍需欧洲议会表决。",
       "据多家媒体报道，谈判在布鲁塞尔持续到周四凌晨。协议文本提高了防务采购和电网改造的拨款，农业补贴总额基本维持不变。"
       "几个净出资国此前要求压缩总额，最终文本在总额上作了小幅下调。欧洲议会预计下月表决。",
       [BBC, GD, DW, NPR], section="top"),
    ev("中东停火协议今天正式生效，首批人道物资车队入境",
       "Ceasefire takes effect as first aid convoys cross border", "world",
       "停火协议于当地时间早上六点生效，联合国称首批人道主义物资车队已通过口岸。",
       "联合国人道事务协调厅表示，首批车队载有食品、药品和燃料。各方将在一周内就第二阶段安排继续磋商。",
       [UN, AJ, BBC], section="top", status="followup",
       change="停火从计划阶段进入实施阶段，首批援助车队已经入境。", prev="2026-11-11"),
    ev("一家头部 AI 公司发布新一代推理模型，开放 API 预览",
       "Sample lab releases next-generation reasoning model in API preview", "ai",
       "公司在官方博客宣布新一代推理模型，称在数学与编程基准上明显提升，并向开发者开放 API 预览。",
       "博客文章介绍了模型的上下文长度、价格和速率限制，并附有安全评估摘要。公司表示正式版将在数周后推出，预览期间价格按标准档的一半计。",
       [OPENAI, VERGE], section="top"),
    ev("日本央行维持利率不变，暗示明年初可能加息",
       "Bank of Japan holds rates, signals possible hike early next year", "finance",
       "日本央行维持短期利率不变，但行长在记者会上表示，如果工资增长持续，明年初可能再次加息。",
       "会后日元兑美元小幅走强。多位分析人士认为，春季劳资谈判结果将是关键依据。",
       [BBCB, GD]),
    ev("德国联合政府就移民改革法案的分歧仍未弥合",
       "German coalition still split over migration reform bill", "politics",
       "德国执政联盟内部就移民改革法案的核心条款仍有分歧，原定本周的内阁表决推迟。",
       "",
       [DW], basis="snippet"),
    ev("UN climate talks open with focus on adaptation finance",
       "UN climate talks open with focus on adaptation finance", "world",
       "The annual UN climate conference opened on Thursday with developing countries pressing for a clear adaptation finance target.",
       "",
       [UN, S("示例来源（非 http 链接）", "javascript:void(0)")], degraded=True),
    ev("南美多国外长会议讨论区域电网互联",
       "South American foreign ministers discuss regional grid interconnection", "world",
       "南美多国外长在会议上讨论跨境电网互联方案，计划明年提交可行性研究。",
       "会议声明提到，互联项目首先在两国之间试点，资金来源包括区域开发银行贷款。",
       [AJ, NPR]),
    ev("开源社区发布大型多语言数据集，覆盖 200 多种语言",
       "Open community releases large multilingual dataset covering 200+ languages", "ai",
       "开源社区发布一个覆盖 200 多种语言的文本数据集，并公布了清洗流程和许可证说明。",
       "数据集按语言和来源分片发布，附有去重与过滤脚本。发布方表示欢迎各语言社区参与质量审核。",
       [HF]),
    ev("某手机厂商推迟折叠屏新机发布，称供应链调整",
       "Phone maker delays foldable launch citing supply chain changes", "tech",
       "一家主要手机厂商宣布推迟原定本月的折叠屏新机发布，称原因是关键零部件供应调整。",
       "公司未给出新的发布日期。科技媒体援引供应链消息称，推迟可能在一到两个月之间。",
       [VERGE, ARS]),
    ev("研究人员发现常用开源压缩库存在高危漏洞，已发布修复",
       "Researchers disclose critical flaw in widely used compression library", "tech",
       "安全研究人员披露一个常用开源压缩库的高危漏洞，维护者已发布修复版本并建议尽快升级。",
       "漏洞可能在处理特制文件时导致内存越界写入。主要 Linux 发行版已开始推送更新。",
       [ARS]),
]

HIDDEN = [
    HiddenItem("某国大选计票继续进行", "2026-11-12"),
    HiddenItem("科技公司季度财报季进入尾声", "2026-11-11"),
    HiddenItem("国际油价小幅波动", None),
]


def main():
    if len(sys.argv) != 2:
        print("用法：python tests/sample_issue.py <输出目录>")
        return 2
    site = os.path.abspath(sys.argv[1])
    settings = load_settings(os.path.join(ROOT, "default_settings.json"))
    render_issue(date(2026, 11, 11), EVENTS[6:9], [], settings, site)
    render_issue(date(2026, 11, 12), EVENTS[3:6], HIDDEN[2:], settings, site)
    render_issue(date(2026, 11, 13), EVENTS, HIDDEN, settings, site)
    with tempfile.TemporaryDirectory() as d:
        store = Store(os.path.join(d, "sample.sqlite"))
        with store.conn:
            for day, n, status in (("2026-11-11", 3, "ok"), ("2026-11-12", 3, "degraded"),
                                   ("2026-11-13", len(EVENTS), "ok")):
                store.conn.execute("insert into issues(date, built_at, item_count, hidden_count, status) "
                                   "values (?, ?, ?, 0, ?)", (day, day + "T15:30:00Z", n, status))
        render_index(store, site)
        store.close()
    print(os.path.join(site, "2026-11-13", "index.html"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
