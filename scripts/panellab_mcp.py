#!/usr/bin/env python3
"""Optional stdio MCP entry. Help and configuration parsing require only stdlib."""
import getpass
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.parse
import urllib.request
import warnings

from panellab import API, ClientError, NoRedirect, Parser, base_url, token_from


def parser():
    p = Parser(description='Read PanelLab tasks through MCP over stdio.', allow_abbrev=False)
    p.add_argument('--url', default='http://127.0.0.1:8000', help='PanelLab API URL (default: http://127.0.0.1:8000)')
    p.add_argument('--token-file', help='Credential file (new file for --setup); normal reads can also use PANEL_TOKEN')
    p.add_argument('--allow-insecure', action='store_true', help='Explicitly allow non-loopback HTTP')
    modes = p.add_mutually_exclusive_group()
    modes.add_argument('--setup', action='store_true', help='Sign in privately, verify API access, save a new POSIX token file and print client JSON')
    modes.add_argument('--print-config', action='store_true', help='Print generic client JSON; requires --token-file and does not read it')
    modes.add_argument('--check', action='store_true', help='Check API access and print only ok/server_count; no MCP SDK required')
    return p


def mcp_api(url, token, allow_insecure=False):
    api = API(url, token)
    if urllib.parse.urlsplit(url).scheme == 'http' and not allow_insecure:
        # base_url admitted only loopback here. Keep credentials off network proxies.
        api.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    return api


def configured_api(args):
    """Resolve the existing CLI URL/credential policy without importing the SDK."""
    return mcp_api(base_url(args.url, args.allow_insecure), token_from(args), args.allow_insecure)


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


def hidden_credentials():
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            username = getpass.getpass('PanelLab username (hidden): ')
            password = getpass.getpass('PanelLab password (hidden): ')
        return {'username': username, 'password': password}
    except (getpass.GetPassWarning, EOFError, KeyboardInterrupt, OSError):
        raise ClientError('hidden_input_required', 'Use a terminal that supports hidden input') from None


def save_new_token(target, token):
    # Publish the fully written 0600 inode without replacing a concurrent creator.
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.panellab-', mode='wb') as temporary:
            temporary.write((token + '\n').encode('ascii'))
            os.fchmod(temporary.fileno(), 0o600)
            temporary.flush()
            os.fsync(temporary.fileno())
            os.link(temporary.name, target)
    except FileExistsError:
        raise ClientError('token_file_exists', 'Credential path exists; choose a new filename') from None
    except OSError:
        raise ClientError('save_failed', 'Cannot save the credential; check the directory and available space') from None


def setup(args):
    if os.name != 'posix':
        raise ClientError('unsupported_platform', '--setup requires a POSIX credential file and terminal')
    if args.allow_insecure:
        raise ClientError('insecure_setup', '--setup requires HTTPS or loopback HTTP; omit --allow-insecure')
    if not args.token_file:
        raise ClientError('token_file_required', '--setup requires --token-file with a new filename')
    url = base_url(args.url, False)
    target = Path(args.token_file).expanduser()
    if os.path.lexists(target):
        raise ClientError('token_file_exists', 'Credential path exists; choose a new filename')

    # Resolve only the parent. Exclusive publication must refuse even a dangling symlink.
    target = target.parent.resolve() / target.name
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = target.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o022:
        raise ClientError('unsafe_directory', 'Use a credential directory owned by you and writable only by you')
    config = client_config(args)
    api = mcp_api(url, None)
    result = api.request('POST', '/api/auth/login', body=hidden_credentials())
    token = result.get('token') if isinstance(result, dict) else None
    if (not isinstance(token, str) or not 1 <= len(token) <= 16384
            or not token.isascii() or not token.isprintable() or ' ' in token):
        raise ClientError('invalid_token', 'Login returned an invalid credential')
    api.token = token
    check_api(api)

    save_new_token(target, token)
    return config


def main(argv=None):
    args = None
    try:
        args = parser().parse_args(argv)
        if args.setup or args.print_config:
            config = setup(args) if args.setup else client_config(args)
            print(json.dumps(config, ensure_ascii=False))
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
        if args is not None and (args.check or args.setup) and type(status) is int and 100 <= status <= 599:
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
