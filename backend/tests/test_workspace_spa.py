"""A bookmarked workspace must survive a fresh request to the packaged API."""
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_workspace_deep_links_and_missing_assets(tmp_path):
    from main import mount_frontend
    (tmp_path / 'index.html').write_text('workspace shell')
    (tmp_path / 'favicon.ico').write_bytes(b'icon')
    app = FastAPI()
    mount_frontend(app, tmp_path)
    client = TestClient(app)
    for route in ('/', '/login', '/workspaces', '/workspaces?view=tasks', '/workspaces/', '/login/'):
        response = client.get(route)
        assert response.status_code == 200
        assert response.text == 'workspace shell'
        assert response.headers['content-type'].startswith('text/html')
    assert client.get('/favicon.ico').content == b'icon'
    for missing in ('/api/missing', '/assets/missing.js', '/unknown'):
        assert client.get(missing).status_code == 404
