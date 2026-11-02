"""Durable metadata and remote read boundaries; only SSH transport is faked."""
import errno
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
        if path.endswith('/missing'): raise FileNotFoundError(errno.ENOENT, "missing")
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

def test_file_confinement_regular_only_and_bounded_tail_download(workspace):
    client,sftp,_ = workspace
    task = create(workspace)
    base = f'/api/workloads/{task}'
    assert client.get(base+'/files').json()['cwd'] == ROOT
    for path in ('/outside/key',ROOT+'/../key',ROOT+'/link/key',ROOT+'/fifo'):
        assert client.get(base+'/text',params={'path':path}).status_code == 403
    assert client.get(base+'/text',params={'path':ROOT+'/missing'}).status_code == 404
    sftp.data[LOG] = b'a'*70000 + b'END'
    tail = client.get(base+'/text',params={'path':LOG}).json()
    assert len(tail['text'].encode()) == 65536
    assert tail['text'].endswith('END') and tail['truncated']
    download = client.get(base+'/download',params={'path':LOG})
    assert download.content == sftp.data[LOG]
    assert "filename*=UTF-8''" in download.headers['content-disposition']
    sftp.data[LOG] = b'a'*(16*1024*1024+1)
    assert client.get(base+'/download',params={'path':LOG}).status_code == 413

def test_reported_runs_status_time_cursor_and_cascade(workspace):
    client,_,state = workspace
    task = create(workspace)
    base = f'/api/workloads/{task}/runs'
    body = {'started_at':'2026-11-03T00:00:00+08:00','finished_at':'2026-11-03T00:01:00+08:00','status':'succeeded','exit_code':0,'summary':'reported by test'}
    first = client.post(base,json=body)
    assert first.status_code == 200,first.text
    assert first.json()['source'] == 'reported' and first.json()['reported_by'] == 1
    second = client.post(base,json={**body,'status':'failed','exit_code':0})
    assert second.status_code == 200
    assert client.get(base,params={'limit':1}).json()['items'][0]['id'] == second.json()['id']
    assert client.get(base,params={'before_id':second.json()['id']}).json()['items'][0]['id'] == first.json()['id']
    for change in ({'started_at':'2026-11-03T00:00:00'},{'finished_at':'2026-11-02T00:00:00Z'},{'status':'running'},{'status':'succeeded','finished_at':None},{'source':'observed'},{'artifact_path':'/outside/file'}):
        assert client.post(base,json={**body,**change}).status_code in (403,422)
    assert client.get(base,params={'limit':51}).status_code == 422
    state['offline'] = True
    assert client.post(base,json=body).status_code == 200
    assert client.delete(f'/api/workloads/{task}').status_code == 200
    from models.workload import ReportedRun
    with SessionLocal() as db: assert db.query(ReportedRun).count() == 0
    assert client.delete('/api/servers/71').status_code == 200


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


def test_read_growth_is_bounded_and_reported(workspace,monkeypatch):
    client,sftp,_ = workspace
    task = create(workspace)
    class Growing(RemoteFile):
        def read(self,n):
            assert 0 < n <= 16*1024*1024+1
            position = self.tell()
            self.seek(0,2)
            self.write(b'grown')
            self.seek(position)
            return super().read(n)
    monkeypatch.setattr(sftp,'open',lambda path,mode:Growing(sftp.data[path]))
    result = client.get(f'/api/workloads/{task}/text',params={'path':LOG})
    assert result.status_code == 200
    assert result.json()['changed_during_read'] is True
    download = client.get(f'/api/workloads/{task}/download',params={'path':LOG})
    assert download.headers['x-file-changed-during-read'] == 'true'


def test_bound_unavailable_creation_and_log_missing_unbound(workspace):
    client,_,state = workspace
    task = create(workspace)
    assert client.get(f'/api/workloads/{task}/observation').json()['process_status'] == 'unbound'
    state['status'] = 'unavailable'
    response = client.patch(f'/api/workloads/{task}',json={'pid':55,'process_identity':'boot:99'})
    assert response.status_code == 503
    assert client.get(f'/api/workloads/{task}').json()['pid'] is None


def test_lost_sftp_during_log_lookup_is_unavailable_not_fresh_missing(workspace,monkeypatch):
    client,sftp,_ = workspace
    task = create(workspace)
    def disconnected(path): raise OSError('Socket is closed; private transport detail')
    monkeypatch.setattr(sftp,'lstat',disconnected)
    result = client.get(f'/api/workloads/{task}/observation')
    assert result.status_code == 503
    assert 'private transport detail' not in result.text
    assert 'observed_at' not in result.json()
    assert client.get(f'/api/workloads/{task}/text',params={'path':LOG}).status_code == 503
