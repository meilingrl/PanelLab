#!/usr/bin/env python3
"""PanelLab's stdlib-only client. All operations use the authenticated HTTP API."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request

JSON_LIMIT = 1024 * 1024
DOWNLOAD_LIMIT = 16 * 1024 * 1024


class ClientError(Exception):
    def __init__(self, code, message, status=None):
        self.error = {'code':code, 'message':message}
        if status is not None: self.error['status'] = status


class Parser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's default message echoes arguments, which can contain secrets.
        raise ClientError('invalid_arguments', 'Invalid command arguments; use --help')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl): return None


def parser():
    p = Parser(description='Read remote workloads and record metadata through the PanelLab API.', allow_abbrev=False,
               epilog='Examples: tasks create --json task.json; observe 7; logs 7; report 7 --json -')
    p.add_argument('--url', default='http://127.0.0.1:8000', help='API base URL (default: http://127.0.0.1:8000)')
    p.add_argument('--token-file', help='Read bearer token from a file; otherwise use PANEL_TOKEN')
    p.add_argument('--allow-insecure', action='store_true', help='Explicitly allow HTTP to a non-loopback host')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('servers', help='List owned servers')
    tasks = sub.add_parser('tasks', help='Durable workload metadata').add_subparsers(dest='action',required=True)
    tasks.add_parser('list')
    for name in ('show','create','update','remove'):
        task = tasks.add_parser(name)
        if name != 'create': task.add_argument('id',type=int)
        if name in ('create','update'): task.add_argument('--json',required=True,metavar='PATH',help='JSON file or - for stdin')
        if name == 'remove': task.add_argument('--yes',action='store_true',help='Confirm metadata deletion')
    for name in ('observe','files','logs','download','runs','report'):
        command = sub.add_parser(name)
        command.add_argument('id',type=int)
        if name == 'files': command.add_argument('path',nargs='?')
        if name == 'download':
            command.add_argument('path')
            command.add_argument('--output',required=True,help='New local file; existing paths are never overwritten')
        if name == 'runs':
            command.add_argument('--before-id',type=int)
            command.add_argument('--limit',type=int,default=20)
        if name == 'report': command.add_argument('--json',required=True,metavar='PATH')
    return p


def base_url(value, allow_insecure):
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment or any(c.isspace() for c in value):
            raise ValueError()
        parsed.port
        loopback = parsed.hostname.lower() == 'localhost'
        try: loopback = loopback or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError: pass
        if parsed.scheme == 'http' and not loopback and not allow_insecure:
            raise ClientError('insecure_url','Non-loopback HTTP requires --allow-insecure')
        return value.rstrip('/')
    except ValueError: raise ClientError('invalid_url','A valid HTTP(S) base URL without credentials is required') from None


def token_from(args):
    try:
        if args.token_file:
            with open(args.token_file,encoding='utf-8') as stream: token = stream.read(16385).strip()
        else: token = os.environ.get('PANEL_TOKEN','').strip()
    except (OSError,UnicodeError): raise ClientError('token_unavailable','Cannot read token file') from None
    if not token: raise ClientError('token_required','Set PANEL_TOKEN or --token-file')
    if len(token)>16384 or not token.isascii() or any(c.isspace() or ord(c)<32 for c in token):
        raise ClientError('invalid_token','Bearer token has invalid format')
    return token


def json_input(path):
    try:
        if path == '-': raw = sys.stdin.read(JSON_LIMIT+1)
        else:
            with open(path,encoding='utf-8') as stream: raw = stream.read(JSON_LIMIT+1)
        if len(raw.encode('utf-8'))>JSON_LIMIT: raise ValueError()
        value = json.loads(raw)
        if not isinstance(value,dict): raise ValueError()
        return value
    except (OSError,ValueError,UnicodeError):
        raise ClientError('invalid_json','Input must be a JSON object of at most 1 MiB') from None


class API:
    def __init__(self,url,token):
        self.url, self.token = url,token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self,method,path,*,body=None,query=None,binary=False):
        url = self.url + path
        if query: url += '?' + urllib.parse.urlencode(query)
        request = urllib.request.Request(url,method=method,data=None if body is None else json.dumps(body).encode('utf-8'),
                                         headers={'Authorization':'Bearer '+self.token,'Accept':'application/json','Content-Type':'application/json'})
        try:
            with self.opener.open(request,timeout=30) as response:
                limit = DOWNLOAD_LIMIT if binary else JSON_LIMIT
                data = response.read(limit+1)
                if len(data)>limit: raise ClientError('response_too_large','API response exceeded the client limit')
                if binary: return data
                return json.loads(data)
        except urllib.error.HTTPError as exc:
            # Do not echo untrusted response bodies, URLs, auth headers or exceptions.
            exc.close()
            raise ClientError('http_error','API request failed',status=exc.code) from None
        except (urllib.error.URLError,OSError,TimeoutError):
            raise ClientError('connection_failed','Cannot reach the API') from None
        except (ValueError,UnicodeError):
            raise ClientError('invalid_response','API returned invalid JSON') from None


def execute(args):
    url = base_url(args.url,args.allow_insecure)
    api = API(url,token_from(args))
    if hasattr(args,'id') and args.id<=0: raise ClientError('invalid_arguments','ID must be positive')
    if args.command == 'servers': return api.request('GET','/api/servers')
    if args.command == 'tasks':
        base = '/api/workloads'
        if args.action == 'list': return api.request('GET',base)
        if args.action == 'create': return api.request('POST',base,body=json_input(args.json))
        path = base+'/'+str(args.id)
        if args.action == 'show': return api.request('GET',path)
        if args.action == 'update': return api.request('PATCH',path,body=json_input(args.json))
        if not args.yes: raise ClientError('confirmation_required','Metadata removal requires --yes')
        return api.request('DELETE',path)
    base = '/api/workloads/'+str(args.id)
    if args.command == 'observe': return api.request('GET',base+'/observation')
    if args.command == 'files': return api.request('GET',base+'/files',query={'path':args.path} if args.path is not None else None)
    if args.command == 'logs':
        task = api.request('GET',base)
        path = task.get('log_path')
        if not isinstance(path,str) or not path: raise ClientError('log_unbound','Workload has no log path')
        return api.request('GET',base+'/text',query={'path':path})
    if args.command == 'runs':
        if not 1<=args.limit<=50 or (args.before_id is not None and args.before_id<=0):
            raise ClientError('invalid_arguments','Run limit must be 1..50 and cursor positive')
        query = {'limit':args.limit}
        if args.before_id is not None: query['before_id'] = args.before_id
        return api.request('GET',base+'/runs',query=query)
    if args.command == 'report': return api.request('POST',base+'/runs',body=json_input(args.json))
    if args.command == 'download':
        output = Path(args.output)
        if output.exists() or output.is_symlink(): raise ClientError('output_exists','Output already exists; choose a new path')
        data = api.request('GET',base+'/download',query={'path':args.path},binary=True)
        try:
            with output.open('xb') as stream: stream.write(data)
        except FileExistsError: raise ClientError('output_exists','Output already exists; choose a new path') from None
        except OSError: raise ClientError('output_failed','Cannot write the output file') from None
        return {'output':str(output),'bytes':len(data)}


def main(argv=None):
    try:
        result = execute(parser().parse_args(argv))
        print(json.dumps(result,ensure_ascii=False))
        return 0
    except ClientError as exc:
        print(json.dumps({'error':exc.error},ensure_ascii=False))
        return 1
    except (OSError,ValueError,TypeError):
        print(json.dumps({'error':{'code':'client_error','message':'Client operation failed'}}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
