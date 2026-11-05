#!/usr/bin/env python3
"""Optional stdio MCP entry. Help and configuration parsing require only stdlib."""
import json
from pathlib import Path
import sys

from panellab import API, ClientError, Parser, base_url, token_from


def parser():
    p = Parser(description='Read PanelLab tasks through MCP over stdio.', allow_abbrev=False)
    p.add_argument('--url', default='http://127.0.0.1:8000', help='PanelLab API URL (default: http://127.0.0.1:8000)')
    p.add_argument('--token-file', help='Read bearer credential from file; otherwise use PANEL_TOKEN')
    p.add_argument('--allow-insecure', action='store_true', help='Explicitly allow non-loopback HTTP')
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--print-config', action='store_true', help='Print generic client JSON; requires --token-file and does not read it')
    modes.add_argument('--check', action='store_true', help='Check API access and print only ok/server_count; no MCP SDK required')
    return p


def configured_api(args):
    """Resolve the existing CLI URL/credential policy without importing the SDK."""
    return API(base_url(args.url, args.allow_insecure), token_from(args))


def client_config(args):
    """Generate paths only: never load a credential or modify a client host."""
    url = base_url(args.url, args.allow_insecure)
    if not args.token_file:
        raise ClientError('token_file_required', '--print-config requires --token-file')
    command_args = [str(Path(__file__).resolve()), '--url', url,
                    '--token-file', str(Path(args.token_file).expanduser().resolve())]
    if args.allow_insecure:
        command_args.append('--allow-insecure')
    # Do not resolve the Python symlink: a virtualenv must retain its interpreter path.
    return {'mcpServers': {'panellab': {'command': str(Path(sys.executable).absolute()), 'args': command_args}}}


def check_api(api):
    """Check the authenticated server-list endpoint without exposing its contents."""
    value = api.request('GET', '/api/servers')
    if (not isinstance(value, dict) or not isinstance(value.get('items'), list)
            or not all(isinstance(item, dict) for item in value['items'])):
        raise ClientError('invalid_response', 'API returned an invalid server list')
    return {'ok': True, 'server_count': len(value['items'])}


def main(argv=None):
    args = None
    try:
        args = parser().parse_args(argv)
        if args.print_config:
            print(json.dumps(client_config(args), ensure_ascii=False))
            return 0
        api = configured_api(args)
        if args.check:
            print(json.dumps(check_api(api)))
            return 0
        try:
            from panellab_mcp_server import build_server
        except ImportError:
            print('MCP dependency unavailable. Install: python -m pip install -r scripts/requirements-mcp.txt', file=sys.stderr)
            return 1
        build_server(api).run(transport='stdio')
        return 0
    except ClientError as exc:
        # ClientError messages are fixed local diagnostics, never response bodies or tokens.
        message = exc.error['message']
        status = exc.error.get('status')
        if args is not None and args.check and type(status) is int and 100 <= status <= 599:
            hint = {401: 'Sign in again and update the configured credential.',
                    403: 'Check account access in the PanelLab interface.',
                    404: 'Check the API URL and endpoint compatibility.',
                    409: 'Check API configuration in the PanelLab interface.'}.get(status, 'Check API availability and configuration.')
            message += f' (HTTP {status}). ' + hint
        print('PanelLab MCP: ' + message, file=sys.stderr)
        return 1
    except Exception:
        print('PanelLab MCP startup failed; check local configuration and installation.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
