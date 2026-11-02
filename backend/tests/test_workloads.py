"""Durable metadata and remote read boundaries; only SSH transport is faked."""
import io
import stat
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from database import SessionLocal
from models.server import Server
from models.user import User
from routers.auth import _create_token
ROOT = "/home/lab/run 中文's"
LOG = ROOT + '/train.log'
class RemoteFile(io.BytesIO):
    def stat(self): return SimpleNamespace(st_mode=stat.S_IFREG, st_size=len(self.getvalue()), st_mtime=123)
class SFTP:
    def __init__(self): self.data = {LOG: b'first\nlast\n'}
    def normalize(self, path): return path
    def lstat(self, path):
        if path.endswith('/missing'): raise FileNotFoundError()
        mode = stat.S_IFREG if path in self.data else stat.S_IFDIR
        if '/link' in path: mode = stat.S_IFLNK
        if '/fifo' in path: mode = stat.S_IFIFO
        return SimpleNamespace(st_mode=mode, st_size=len(self.data.get(path, b'')), st_mtime=123)
    def open(self, path, mode): return RemoteFile(self.data[path])
    def listdir_iter(self, path, read_aheads):
        yield SimpleNamespace(filename='train.log', st_mode=stat.S_IFREG, st_size=11, st_mtime=123)
@pytest.fixture
def workspace(client, monkeypatch):
    with SessionLocal() as db:
        db.add(User(id=2, username='other', password_hash='unused'))
        db.add(Server(id=71, user_id=1, name='lab', host='fixture.invalid', port=22, username='lab'))
        db.add(Server(id=72, user_id=2, name='other', host='fixture.invalid', port=22, username='lab'))
        db.commit()
    client.headers['Authorization'] = 'Bearer ' + _create_token('admin')
    from services import remote_connection, remote_files, remote_probe
    sftp = SFTP()
    state = {'status':'present','identity':'boot:99','offline':False,'calls':0}
    @contextmanager
    def connection(db, user, server_id):
        remote_connection.owned_server(db, user, server_id)
        state['calls'] += 1
        if state['offline']: raise HTTPException(503, detail={'code':'connection_failed','message':'Unavailable'})
        yield object()
    @contextmanager
    def open_sftp(client): yield sftp
    def snapshot(client, *, pid=None, query=None):
        process = {'pid':pid,'identity':state['identity'],'command':'python train.py','cpu':None}
        return {'observed_at':'2026-11-03T00:00:00+00:00','system':{'cpu':None,'memory':None,'network':None,'gpu':None},'processes':[],'capabilities':{'processes':True,'processes_truncated':True},'warnings':[],'target_process':{'pid':pid,'status':state['status'],'process':process if state['status']=='present' else None}}
    monkeypatch.setattr(remote_connection,'open_remote',connection)
    monkeypatch.setattr(remote_files,'open_sftp',open_sftp)
    monkeypatch.setattr(remote_probe,'collect_snapshot',snapshot)
    return client,sftp,state

def create(workspace, **extra):
    client,_,_ = workspace
    payload = dict(name='training',kind='training',server_id=71,root_path=ROOT,log_path=LOG)
    payload.update(extra)
    response = client.post('/api/workloads',json=payload)
    assert response.status_code == 200,response.text
    return response.json()['id']

def test_metadata_ownership_partial_updates_and_server_delete_guard(workspace):
    client,_,state = workspace
    task = create(workspace)
    assert client.get('/api/workloads').json()['items'][0]['id'] == task
    state['offline'] = True
    assert client.patch(f'/api/workloads/{task}',json={'notes':'offline edit'}).status_code == 200
    assert client.delete('/api/servers/71').status_code == 409
    before = state['calls']
    client.headers['Authorization'] = 'Bearer ' + _create_token('other')
    for suffix in ('','/observation','/files','/text?path='+LOG,'/download?path='+LOG,'/runs'):
        assert client.get(f'/api/workloads/{task}'+suffix).status_code == 404
    assert client.patch(f'/api/workloads/{task}',json={'name':'steal'}).status_code == 404
    assert client.delete(f'/api/workloads/{task}').status_code == 404
    assert state['calls'] == before

def test_binding_process_identity_and_unavailable_are_not_missing(workspace):
    client,_,state = workspace
    task = create(workspace,pid=99999,process_identity='boot:99')
    path = f'/api/workloads/{task}/observation'
    assert client.get(path).json()['process_status'] == 'running'
    state['identity'] = 'boot:100'
    assert client.get(path).json()['process_status'] == 'exited_or_missing'
    assert client.patch(f'/api/workloads/{task}',json={'process_identity':'boot:101'}).status_code == 409
    state['status'] = 'unavailable'
    assert client.get(path).json()['process_status'] == 'unknown'
    state['status'] = 'missing'
    assert client.get(path).json()['process_status'] == 'exited_or_missing'
    state['offline'] = True
    assert client.get(path).status_code == 503

@pytest.mark.parametrize('fields',[{'pid':4},{'root_path':'relative'},{'log_path':'/outside'},{'website_url':'http://user:pass@example.com'},{'owner_id':2},{'name':' '},{'kind':'shell'}])
def test_invalid_metadata_rejected(workspace,fields):
    client,_,_ = workspace
    payload = dict(name='task',kind='website',server_id=71,root_path=ROOT)
    payload.update(fields)
    assert client.post('/api/workloads',json=payload).status_code in (403,422)

def test_zombie_and_stopped_process_are_not_running(workspace,monkeypatch):
    client,_,_ = workspace
    task = create(workspace,pid=999,process_identity='boot:99')
    from services import remote_probe
    original = remote_probe.collect_snapshot
    proc_state = ['Z']
    def snapshot(*args,**kwargs):
        value = original(*args,**kwargs)
        value['target_process']['process']['state'] = proc_state[0]
        return value
    monkeypatch.setattr(remote_probe,'collect_snapshot',snapshot)
    result = client.get(f'/api/workloads/{task}/observation').json()
    assert result['process_status'] == 'exited_or_missing'
    assert result['warnings']
    proc_state[0] = 'T'
    result = client.get(f'/api/workloads/{task}/observation').json()
    assert result['process_status'] == 'unknown'
    assert result['warnings']


def test_cross_owner_creation_and_rebinding_do_not_connect(workspace):
    client,_,state = workspace
    task = create(workspace)
    calls = state['calls']
    assert client.patch(f'/api/workloads/{task}',json={'server_id':72}).status_code == 404
    assert client.post('/api/workloads',json={'name':'other','kind':'training','server_id':72,'root_path':ROOT}).status_code == 404
    assert state['calls'] == calls
    state['offline'] = True
    assert client.patch(f'/api/workloads/{task}',json={'root_path':'/new','log_path':None}).status_code == 503
    assert client.get(f'/api/workloads/{task}').json()['root_path'] == ROOT


def test_bound_unavailable_creation_and_log_missing_unbound(workspace):
    client,_,state = workspace
    task = create(workspace)
    assert client.get(f'/api/workloads/{task}/observation').json()['process_status'] == 'unbound'
    state['status'] = 'unavailable'
    response = client.patch(f'/api/workloads/{task}',json={'pid':55,'process_identity':'boot:99'})
    assert response.status_code == 503
    assert client.get(f'/api/workloads/{task}').json()['pid'] is None
