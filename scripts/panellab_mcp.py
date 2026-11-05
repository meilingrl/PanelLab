#!/usr/bin/env python3
"""Optional stdio MCP entry. Help and configuration parsing require only stdlib."""
import sys

from panellab import API, ClientError, Parser, base_url, token_from


def parser():
    p = Parser(description='Read PanelLab tasks through MCP over stdio.', allow_abbrev=False)
    p.add_argument('--url', default='http://127.0.0.1:8000', help='PanelLab API URL (default: http://127.0.0.1:8000)')
    p.add_argument('--token-file', help='Read bearer credential from file; otherwise use PANEL_TOKEN')
    p.add_argument('--allow-insecure', action='store_true', help='Explicitly allow non-loopback HTTP')
    return p


def configured_api(args):
    """Resolve the existing CLI URL/credential policy without importing the SDK."""
    return API(base_url(args.url, args.allow_insecure), token_from(args))


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        api = configured_api(args)
        try:
            from panellab_mcp_server import build_server
        except ImportError:
            print('MCP dependency unavailable. Install: python -m pip install -r scripts/requirements-mcp.txt', file=sys.stderr)
            return 1
        build_server(api).run(transport='stdio')
        return 0
    except ClientError as exc:
        # ClientError messages are fixed local diagnostics, never response bodies or tokens.
        print('PanelLab MCP: ' + exc.error['message'], file=sys.stderr)
        return 1
    except Exception:
        print('PanelLab MCP startup failed; check local configuration and installation.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
