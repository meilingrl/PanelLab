"""HTTP contracts backed by SQLite and temporary config files."""
import jwt
import pytest
from config import get_settings
from database import SessionLocal
from models.site_config import SiteConfig
from models.user import User
from routers.auth import _create_token

CONFIG = dict(name='web', domain='web.example.com', site_type='static', root_path='/var/www/web', proxy_target='', listen_port=8080, enabled=True)
@pytest.fixture
def env(client, tmp_path, monkeypatch):
    monkeypatch.setenv('NGINX_CONF_DIR', str(tmp_path / 'conf'))
    monkeypatch.setenv('OPERATOR_USERNAMES', 'admin,other')
    token = client.post('/api/auth/login', json=dict(username='admin', password='admin')).json()['token']
    return client, {'Authorization': 'Bearer ' + token}, tmp_path

def preview(env, operation='create', site_id=None, config=CONFIG):
    client, headers, _ = env
    body = dict(operation=operation)
    if site_id is not None: body['site_id'] = site_id
    if operation in ('create', 'update'): body['config'] = config
    r = client.post('/api/site-changes/preview', headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()

def execute(env, p):
    return env[0].post('/api/site-changes/execute', headers=env[1], json={'token': p['token']})

def test_preview_is_read_only_and_simulation_is_never_active(env):
    p = preview(env)
    assert p['before'] is None and 'server_name web.example.com;' in p['after']
    assert '+    root /var/www/web;' in p['diff'] and p['simulated'] is True
    assert not (env[2] / 'conf').exists()
    assert env[0].get('/api/sites', headers=env[1]).json() == {'items': []}
    assert env[0].get('/api/site-changes', headers=env[1]).json() == {'items': []}
    r = execute(env, p); assert r.status_code == 200, r.text
    assert r.json()['change']['status'] == 'simulated'
    assert r.json()['site']['status'] == 'simulated'
    assert not (env[2] / 'conf').exists()

@pytest.mark.parametrize('method,path', [('post','/api/sites'), ('put','/api/sites/1'), ('delete','/api/sites/1'), ('post','/api/sites/1/apply')])
def test_old_write_routes_require_preview(env, method, path):
    assert env[0].request(method, path, headers=env[1], json=CONFIG).status_code == 428

def test_competing_previews_replay_and_external_drift(env):
    p1, p2 = preview(env), preview(env)
    assert execute(env,p1).status_code == 200
    assert execute(env,p1).status_code == 409
    assert execute(env,p2).status_code == 409
    row = env[0].get('/api/sites', headers=env[1]).json()['items'][0]
    p = preview(env,'apply',row['id'])
    (env[2] / 'conf').mkdir(); (env[2] / 'conf/handwritten.conf').write_text('server {}')
    assert execute(env,p).status_code == 409

@pytest.mark.parametrize('mutation', ['expired', 'tampered', 'purpose', 'extra', 'candidate'])
def test_invalid_tokens_never_apply(env, mutation):
    p = preview(env)
    claims = jwt.decode(p['token'], get_settings().secret_key, algorithms=['HS256'], audience='site-change')
    if mutation == 'expired': claims['exp'] = 1
    elif mutation == 'purpose': claims['purpose'] = 'login'
    elif mutation == 'extra': claims['unexpected'] = 'x'
    elif mutation == 'candidate': claims['config']['root_path'] = '/tmp; bad'
    token = jwt.encode(claims, get_settings().secret_key, algorithm='HS256')
    if mutation == 'tampered': token = 'x' + token
    assert execute(env, {'token':token}).status_code in (401,422)
    assert env[0].get('/api/sites', headers=env[1]).json() == {'items': []}

def test_preview_token_cannot_authenticate_and_other_owner_cannot_execute(env):
    p = preview(env)
    assert env[0].get('/api/sites', headers={'Authorization':'Bearer '+p['token']}).status_code == 401
    with SessionLocal() as db:
        u = User(username='other',password_hash='unused'); db.add(u); db.commit(); other_id=u.id
    token = _create_token('other')
    r = env[0].post('/api/site-changes/execute',headers={'Authorization':'Bearer '+token},json={'token':p['token']})
    assert r.status_code == 403

@pytest.mark.parametrize('patch', [{'domain':'a;bad'}, {'root_path':'/a\nb'}, {'proxy_target':'http://user:password@localhost'}, {'proxy_target':'http://localhost/?token=secret'}])
def test_preview_rejects_injection_and_embedded_credentials(env, patch):
    config = CONFIG | patch
    if 'proxy_target' in patch: config['site_type']='proxy'
    r = env[0].post('/api/site-changes/preview', headers=env[1], json=dict(operation='create',config=config))
    assert r.status_code == 422

def test_update_delete_and_history_survives_deletion(env):
    site = execute(env,preview(env)).json()['site']
    update = execute(env,preview(env,'update',site['id'],CONFIG | {'root_path':'/var/www/new'}))
    assert update.json()['site']['root_path'] == '/var/www/new'
    deleted = execute(env,preview(env,'delete',site['id'])); assert deleted.status_code == 200
    assert deleted.json()['site'] is None
    history = env[0].get('/api/site-changes?limit=2', headers=env[1]).json()['items']
    assert [r['operation'] for r in history] == ['delete','update']
    assert history[0]['site_id'] == site['id']
    older=env[0].get('/api/site-changes',params={'before_id':history[-1]['id']},headers=env[1]).json()['items']
    assert len(older)==1 and older[0]['operation']=='create'
    with SessionLocal() as db:
        u=User(username='other',password_hash='unused'); db.add(u); db.commit(); other_id=u.id
    assert env[0].get('/api/site-changes',headers={'Authorization':'Bearer '+_create_token('other')}).json()=={'items':[]}

@pytest.mark.parametrize('key,value',[('NGINX_SKIP_APPLY','false'),('NGINX_CONF_DIR','other'),('NGINX_RELOAD_CMD','other-command')])
def test_runtime_setting_changes_require_new_preview(env,monkeypatch,key,value):
    p=preview(env)
    monkeypatch.setenv(key,str(env[2]/value) if key=='NGINX_CONF_DIR' else value)
    assert execute(env,p).status_code==409
    assert not (env[2]/'conf').exists()

def test_database_drift_in_all_config_fields_invalidates_preview(env):
    site=execute(env,preview(env)).json()['site']; p=preview(env,'apply',site['id'])
    with SessionLocal() as db:
        row=db.get(SiteConfig,site['id']); row.enabled=False; row.config_filename='different.conf'; db.commit()
    assert execute(env,p).status_code==409

def test_shared_domain_port_conflict_does_not_disclose_owner(env):
    with SessionLocal() as db:
        u=User(username='other',password_hash='unused'); db.add(u); db.flush()
        db.add(SiteConfig(user_id=u.id,name='private-name',domain=CONFIG['domain'],site_type='static',root_path='/secret',listen_port=8080)); db.commit()
    r=env[0].post('/api/site-changes/preview',headers=env[1],json=dict(operation='create',config=CONFIG))
    assert r.status_code==409
    assert 'private-name' not in r.text and '/secret' not in r.text

def test_final_commit_failure_keeps_pending_and_changed_file_for_manual_review(env,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    from sqlalchemy.orm import Session
    import shlex,sys
    monkeypatch.setenv('NGINX_SKIP_APPLY','false')
    command=shlex.quote(sys.executable)+' -c pass'
    monkeypatch.setenv('NGINX_TEST_CMD',command);monkeypatch.setenv('NGINX_RELOAD_CMD',command)
    p=preview(env)
    original=Session.commit
    def fail_final(session):
        from models.site_change import SiteChange
        if any(isinstance(obj,SiteChange) and obj.status=='applied' for obj in session.identity_map.values()):
            raise SQLAlchemyError('database failure containing secret')
        return original(session)
    monkeypatch.setattr(Session,'commit',fail_final)
    r=execute(env,p)
    assert r.status_code==503 and r.json()['detail']['status']=='manual_review_required'
    assert 'secret' not in r.text
    assert env[0].get('/api/sites',headers=env[1]).json()=={'items':[]}
    history=env[0].get('/api/site-changes',headers=env[1]).json()['items']
    assert len(history)==1 and history[0]['status']=='pending'
    assert 'server_name web.example.com;' in next((env[2]/'conf').glob('*.conf')).read_text()
    assert execute(env,p).status_code==409

def test_nginx_failure_preserves_database_and_records_restoration(env,monkeypatch):
    import shlex,sys
    site=execute(env,preview(env)).json()['site']
    conf=env[2]/'conf';conf.mkdir();target=conf/site['config_filename'];target.write_text('original\n')
    monkeypatch.setenv('NGINX_SKIP_APPLY','false')
    monkeypatch.setenv('NGINX_TEST_CMD',shlex.quote(sys.executable)+' -c "import sys;sys.exit(1)"')
    p=preview(env,'update',site['id'],CONFIG | {'root_path':'/var/www/changed'})
    r=execute(env,p)
    assert r.status_code==503 and r.json()['detail']['status']=='failed_restored'
    assert target.read_text()=='original\n'
    current=env[0].get('/api/sites/'+str(site['id']),headers=env[1]).json()
    assert current['root_path']=='/var/www/web' and current['status']=='simulated'
    history=env[0].get('/api/site-changes',headers=env[1]).json()['items']
    assert history[0]['status']=='failed_restored'
    assert [s['name'] for s in history[0]['steps']]==['write','test','restore']

def test_concurrent_execute_claims_once(env):
    from concurrent.futures import ThreadPoolExecutor
    p=preview(env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=sorted(pool.map(lambda _:execute(env,p).status_code,range(2)))
    assert statuses==[200,409]
    assert len(env[0].get('/api/sites',headers=env[1]).json()['items'])==1
    assert len(env[0].get('/api/site-changes',headers=env[1]).json()['items'])==1

@pytest.mark.parametrize('body',[{'operation':'create','site_id':1,'config':CONFIG},{'operation':'delete','site_id':1,'config':CONFIG},{'operation':'update','site_id':1},{'operation':'apply'}])
def test_operation_payload_is_strict(env,body):
    assert env[0].post('/api/site-changes/preview',headers=env[1],json=body).status_code==422

def test_pending_audit_commit_failure_never_touches_file(env,monkeypatch):
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import SQLAlchemyError
    p=preview(env)
    def fail(_): raise SQLAlchemyError('secret')
    monkeypatch.setattr(Session,'commit',fail)
    assert execute(env,p).status_code==503
    assert not (env[2]/'conf').exists()
    assert env[0].get('/api/site-changes',headers=env[1]).json()=={'items':[]}

def test_token_expiring_while_waiting_for_lock_never_applies(env,monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from services import site_changes
    p=preview(env)
    claims=jwt.decode(p['token'],get_settings().secret_key,algorithms=['HS256'],audience='site-change')
    original=site_changes.NginxManager.change_lock
    @contextmanager
    def elapsed(manager):
        with original(manager):
            monkeypatch.setattr(site_changes,'time',SimpleNamespace(time=lambda:claims['exp']+1))
            yield
    monkeypatch.setattr(site_changes.NginxManager,'change_lock',elapsed)
    assert execute(env,p).status_code==401
    assert env[0].get('/api/sites',headers=env[1]).json()=={'items':[]}
    assert env[0].get('/api/site-changes',headers=env[1]).json()=={'items':[]}
