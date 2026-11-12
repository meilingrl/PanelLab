"""A bookmarked workspace must survive a fresh request to the packaged API."""
import re
from pathlib import Path

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


def test_every_frontend_route_reloads(tmp_path):
    """A browser reload on any page must get the app, not the API's 404."""
    from main import FRONTEND_ROUTES, mount_frontend
    router = Path(__file__).resolve().parents[2] / 'frontend' / 'src' / 'router' / 'index.js'
    paths = {p.strip('/') for p in re.findall(r"path:\s*'([^']*)'", router.read_text(encoding='utf-8'))}
    paths.discard('')
    assert paths == set(FRONTEND_ROUTES)
    (tmp_path / 'index.html').write_text('shell')
    app = FastAPI()
    mount_frontend(app, tmp_path)
    client = TestClient(app)
    for route in paths:
        for url in ('/' + route, '/' + route + '/'):
            response = client.get(url)
            assert response.status_code == 200, url
            assert response.text == 'shell'
