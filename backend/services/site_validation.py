"""Conservative literal-only Nginx input validation, shared by API and renderer."""
import re
from urllib.parse import urlsplit

_HOST = re.compile(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\Z')
_PATH = re.compile(r'/[a-zA-Z0-9_./-]*\Z')


def validate_config(domain, site_type, root_path, proxy_target, listen_port):
    if not isinstance(domain, str) or len(domain) > 253 or not _HOST.fullmatch(domain):
        raise ValueError('域名必须为单个 ASCII 主机名，不支持通配符或指令字符')
    if type(listen_port) is not int or not 1 <= listen_port <= 65535:
        raise ValueError('监听端口范围非法')
    if root_path and not _PATH.fullmatch(root_path):
        raise ValueError('根目录必须为绝对路径，仅支持字母数字及 / . _ -')
    if proxy_target:
        try:
            parsed = urlsplit(proxy_target)
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                    or not _HOST.fullmatch(parsed.hostname) or parsed.username or parsed.password
                    or parsed.query or parsed.fragment or '?' in proxy_target or '#' in proxy_target
                    or (parsed.path and not _PATH.fullmatch(parsed.path))
                    or not re.fullmatch(r'https?://[a-zA-Z0-9.-]+(?::[0-9]+)?(?:/[a-zA-Z0-9_./-]*)?', proxy_target)
                    or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
                raise ValueError()
        except ValueError:
            raise ValueError('代理目标仅支持无凭据、查询参数、片段或变量的 http(s) 主机及安全路径') from None
    if site_type == 'static' and not root_path:
        raise ValueError('静态站点必须填写根目录')
    if site_type == 'proxy' and not proxy_target:
        raise ValueError('反向代理站点必须填写代理目标地址')
    if site_type not in {'static', 'proxy'}:
        raise ValueError('site_type 只能是 static 或 proxy')
