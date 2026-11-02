"""CLI exercises the same HTTP API against a controlled local server."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'panellab.py'


@pytest.fixture
def http_api():
    state = {'requests':[], 'error':False, 'redirect':False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self): self.reply()
        def do_POST(self): self.reply()
        def do_PATCH(self): self.reply()
        def do_DELETE(self): self.reply()
        def reply(self):
            length = int(self.headers.get('Content-Length','0'))
            body = self.rfile.read(length)
            state['requests'].append((self.command,self.path,self.headers.get('Authorization'),json.loads(body) if body else None))
            if state['redirect']:
                self.send_response(302)
                self.send_header('Location','http://127.0.0.1:1/leak')
                self.end_headers()
                return
            self.send_response(503 if state['error'] else 200)
            self.end_headers()
            if state['error']: self.wfile.write(b'{"detail":"PRIVATE_REMOTE_PASSWORD token-secret"}')
            elif '/download?' in self.path: self.wfile.write(b'download\x00data')
            elif self.path == '/api/workloads/7': self.wfile.write(json.dumps({'id':7,'log_path':"/work/a 中文's.log"}).encode())
            else: self.wfile.write(b'{"items":[],"ok":true}')
    server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}',state
    server.shutdown()
    server.server_close()
    thread.join()


def invoke(*args, token='token-secret', input=None):
    env = {**os.environ}
    if token is None: env.pop('PANEL_TOKEN',None)
    else: env['PANEL_TOKEN'] = token
    return subprocess.run([sys.executable,str(SCRIPT),*args],input=input,text=True,capture_output=True,env=env,timeout=10)


def test_cli_uses_bearer_same_api_json_input_and_encoded_paths(http_api):
    url,state = http_api
    result = invoke('--url',url,'tasks','create','--json','-',input='{"name":"train"}')
    assert result.returncode == 0,result.stderr + result.stdout
    assert json.loads(result.stdout)['ok'] is True
    assert state['requests'][-1] == ('POST','/api/workloads','Bearer token-secret',{'name':'train'})
    result = invoke('--url',url,'logs','7')
    assert result.returncode == 0
    assert state['requests'][-1][1] == '/api/workloads/7/text?path=%2Fwork%2Fa+%E4%B8%AD%E6%96%87%27s.log'
    assert invoke('--url',url,'tasks','remove','7').returncode != 0
    assert invoke('--url',url,'tasks','remove','7','--yes').returncode == 0
    assert state['requests'][-1][:2] == ('DELETE','/api/workloads/7')


def test_cli_no_token_insecure_url_and_safe_json_errors(http_api,tmp_path):
    url,state = http_api
    result = invoke('--url',url,'servers',token=None)
    assert result.returncode != 0 and json.loads(result.stdout)['error']['code'] == 'token_required'
    assert not state['requests']
    result = invoke('--url','http://example.com','servers')
    assert result.returncode != 0 and json.loads(result.stdout)['error']['code'] == 'insecure_url'
    state['error'] = True
    result = invoke('--url',url,'observe','7')
    assert result.returncode != 0 and json.loads(result.stdout)['error']['status'] == 503
    assert 'PRIVATE_REMOTE_PASSWORD' not in result.stdout+result.stderr
    assert 'token-secret' not in result.stdout+result.stderr
    tokenfile = tmp_path/'token'
    tokenfile.write_text('from-file\n')
    state['error'] = False
    result = invoke('--url',url,'--token-file',str(tokenfile),'servers',token=None)
    assert result.returncode == 0
    assert state['requests'][-1][2] == 'Bearer from-file'
    result = invoke('--token','secret','servers')
    assert result.returncode != 0 and 'secret' not in result.stdout+result.stderr


def test_cli_download_no_overwrite_and_redirect_no_credential_forwarding(http_api,tmp_path):
    url,state = http_api
    output = tmp_path/'artifact'
    result = invoke('--url',url,'download','7','/work/result','--output',str(output))
    assert result.returncode == 0,result.stdout+result.stderr
    assert output.read_bytes() == b'download\x00data'
    count = len(state['requests'])
    result = invoke('--url',url,'download','7','/work/result','--output',str(output))
    assert result.returncode != 0 and output.read_bytes() == b'download\x00data'
    assert len(state['requests']) == count
    state['redirect'] = True
    result = invoke('--url',url,'servers')
    assert result.returncode != 0 and json.loads(result.stdout)['error']['status'] == 302
