"""读取与校验设置。秘密（API 密钥、SMTP 密码）只来自环境变量，不允许出现在设置文件里。"""
import json
from dataclasses import dataclass, field
from typing import List
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

KINDS = ("rss", "atom", "html")
TYPES = ("agency", "media", "official", "company")
SECURITIES = ("starttls", "ssl", "none")
THEMES = ("light", "dark")
SECRET_KEYS = ("api_key", "password")


class SettingsError(Exception):
    def __init__(self, field, message):
        super().__init__("%s: %s" % (field, message))
        self.field = field
        self.message = message


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    weight: float


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    url: str
    kind: str
    category: str
    type: str
    enabled: bool = True


@dataclass(frozen=True)
class LLMSettings:
    base_url: str
    model: str
    max_calls_per_day: int = 2000
    timeout: int = 60


@dataclass(frozen=True)
class SMTPSettings:
    host: str = ""
    port: int = 0
    security: str = "starttls"
    username: str = ""
    sender: str = ""
    recipients: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Settings:
    timezone: str
    site_url: str
    site_dir: str
    data_dir: str
    categories: List[Category]
    sources: List[Source]
    llm: LLMSettings
    smtp: SMTPSettings
    lookback_days: int = 7
    keywords_follow: List[str] = field(default_factory=list)
    keywords_block: List[str] = field(default_factory=list)
    filter_china_domestic: bool = True
    top_count: int = 8
    per_category: int = 8
    fetch_articles_max: int = 40
    notify_top: int = 5
    default_theme: str = "light"


def _check_no_secrets(obj, path):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in SECRET_KEYS:
                raise SettingsError(
                    "%s.%s" % (path, k) if path else k,
                    "设置文件里不能放密钥或密码，请改用环境变量（DAILYNEWS_LLM_API_KEY、DAILYNEWS_SMTP_PASSWORD）",
                )
            _check_no_secrets(v, "%s.%s" % (path, k) if path else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _check_no_secrets(v, "%s[%d]" % (path, i))


def _str(d, key, path, required=True, default=""):
    v = d.get(key, default if not required else None)
    if v is None or not isinstance(v, str) or (required and not v.strip()):
        raise SettingsError(path + key, "必须是非空文字" if required else "必须是文字")
    return v


def _int(d, key, path, default, minimum=0):
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise SettingsError(path + key, "必须是不小于 %d 的整数" % minimum)
    return v


def _bool(d, key, path, default):
    v = d.get(key, default)
    if not isinstance(v, bool):
        raise SettingsError(path + key, "必须是 true 或 false")
    return v


def _str_list(d, key, path):
    v = d.get(key, [])
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise SettingsError(path + key, "必须是文字列表")
    return list(v)


def _http_url(d, key, path):
    url = _str(d, key, path)
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.netloc:
        raise SettingsError(path + key, "必须是 http 或 https 地址")
    return url


def _obj(d, key):
    v = d.get(key, {})
    if not isinstance(v, dict):
        raise SettingsError(key, "必须是对象")
    return v


def load_settings(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except OSError as e:
        raise SettingsError("settings", "无法读取 %s：%s" % (path, e.strerror or e))
    except ValueError as e:
        raise SettingsError("settings", "不是合法的 JSON：%s" % e)
    if not isinstance(raw, dict):
        raise SettingsError("settings", "顶层必须是对象")
    _check_no_secrets(raw, "")

    tz = _str(raw, "timezone", "")
    try:
        if ZoneInfo is None:
            raise ValueError("no zoneinfo")
        ZoneInfo(tz)
    except Exception:
        raise SettingsError("timezone", "不是有效的时区名称：%s" % tz)

    cats_raw = raw.get("categories")
    if not isinstance(cats_raw, list) or not cats_raw:
        raise SettingsError("categories", "必须是非空列表")
    categories, cat_ids = [], set()
    for i, c in enumerate(cats_raw):
        p = "categories[%d]." % i
        if not isinstance(c, dict):
            raise SettingsError("categories[%d]" % i, "必须是对象")
        cid = _str(c, "id", p)
        if cid in cat_ids:
            raise SettingsError(p + "id", "类别 id 重复：%s" % cid)
        cat_ids.add(cid)
        w = c.get("weight", 1.0)
        if isinstance(w, bool) or not isinstance(w, (int, float)) or w < 0:
            raise SettingsError(p + "weight", "必须是不小于 0 的数字")
        categories.append(Category(cid, _str(c, "name", p), float(w)))

    src_raw = raw.get("sources")
    if not isinstance(src_raw, list) or not src_raw:
        raise SettingsError("sources", "必须是非空列表")
    sources, src_ids = [], set()
    for i, s in enumerate(src_raw):
        p = "sources[%d]." % i
        if not isinstance(s, dict):
            raise SettingsError("sources[%d]" % i, "必须是对象")
        sid = _str(s, "id", p)
        if sid in src_ids:
            raise SettingsError(p + "id", "来源 id 重复：%s" % sid)
        src_ids.add(sid)
        url = _http_url(s, "url", p)
        kind = _str(s, "kind", p)
        if kind not in KINDS:
            raise SettingsError(p + "kind", "必须是 %s 之一" % "/".join(KINDS))
        category = _str(s, "category", p)
        if category not in cat_ids:
            raise SettingsError(p + "category", "未知类别：%s" % category)
        stype = _str(s, "type", p)
        if stype not in TYPES:
            raise SettingsError(p + "type", "必须是 %s 之一" % "/".join(TYPES))
        sources.append(Source(sid, _str(s, "name", p), url, kind, category, stype,
                              _bool(s, "enabled", p, True)))

    l = _obj(raw, "llm")
    llm = LLMSettings(
        base_url=_http_url(l, "base_url", "llm."),
        model=_str(l, "model", "llm."),
        max_calls_per_day=_int(l, "max_calls_per_day", "llm.", 2000, 1),
        timeout=_int(l, "timeout", "llm.", 60, 1),
    )

    # SMTP 只在填写了 host 时才校验；默认文件里留空，由 notify 在发信时要求完整。
    m = _obj(raw, "smtp")
    host = _str(m, "host", "smtp.", required=False)
    security = _str(m, "security", "smtp.", required=False, default="starttls") or "starttls"
    recipients = _str_list(m, "recipients", "smtp.")
    smtp = SMTPSettings(
        host=host,
        port=_int(m, "port", "smtp.", 0, 0),
        security=security,
        username=_str(m, "username", "smtp.", required=False),
        sender=_str(m, "sender", "smtp.", required=False),
        recipients=recipients,
    )
    if host:
        if security not in SECURITIES:
            raise SettingsError("smtp.security", "必须是 %s 之一" % "/".join(SECURITIES))
        if not smtp.sender:
            raise SettingsError("smtp.sender", "填写了 host 时必须填写发件人")
        if not recipients:
            raise SettingsError("smtp.recipients", "填写了 host 时至少需要一个收件人")

    theme = _str(raw, "default_theme", "", required=False, default="light") or "light"
    if theme not in THEMES:
        raise SettingsError("default_theme", "必须是 %s 之一" % "/".join(THEMES))

    return Settings(
        timezone=tz,
        site_url=_http_url(raw, "site_url", ""),
        site_dir=_str(raw, "site_dir", ""),
        data_dir=_str(raw, "data_dir", ""),
        categories=categories,
        sources=sources,
        llm=llm,
        smtp=smtp,
        lookback_days=_int(raw, "lookback_days", "", 7, 1),
        keywords_follow=_str_list(raw, "keywords_follow", ""),
        keywords_block=_str_list(raw, "keywords_block", ""),
        filter_china_domestic=_bool(raw, "filter_china_domestic", "", True),
        top_count=_int(raw, "top_count", "", 8, 1),
        per_category=_int(raw, "per_category", "", 8, 1),
        fetch_articles_max=_int(raw, "fetch_articles_max", "", 40, 0),
        notify_top=_int(raw, "notify_top", "", 5, 1),
        default_theme=theme,
    )
