"""Private-key SSH login: the key is accepted once, stored encrypted, never echoed."""
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from database import SessionLocal
from models.server import Server
from models.server_key import ServerPrivateKey
from models.user import User
from routers.auth import _create_token
from tests.test_remote_connection import FakeTransport, network, pin, remote  # noqa: F401


def openssh(key, passphrase=None):
    encryption = serialization.BestAvailableEncryption(passphrase.encode()) if passphrase else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, encryption).decode()


def legacy_pem(key, passphrase=None):
    encryption = serialization.BestAvailableEncryption(passphrase.encode()) if passphrase else serialization.NoEncryption()
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, encryption).decode()


@pytest.fixture
def auth_events(monkeypatch):
    def auth_publickey(self, username, key):
        FakeTransport.events.append(('publickey', username, key.get_name(), key.get_base64()))
    monkeypatch.setattr(FakeTransport, 'auth_publickey', auth_publickey, raising=False)
    return FakeTransport.events


def put_key(remote, text, passphrase=None, server=71):
    body = {'private_key': text}
    if passphrase is not None: body['passphrase'] = passphrase
    return remote.put(f'/api/servers/{server}/private-key', json=body)


def code(response):
    return response.json()['detail']['code']


@pytest.mark.parametrize('make, kind', [
    (lambda: openssh(ed25519.Ed25519PrivateKey.generate()), 'ssh-ed25519'),
    (lambda: openssh(ec.generate_private_key(ec.SECP256R1())), 'ecdsa-sha2-nistp256'),
    (lambda: legacy_pem(rsa.generate_private_key(65537, 2048)), 'ssh-rsa'),
])
def test_supported_keys_are_stored_encrypted_and_never_returned(remote, make, kind):
    text = make()
    result = put_key(remote, text)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['configured'] and body['key_type'] == kind and body['fingerprint'].startswith('SHA256:')
    assert 'PRIVATE KEY' not in result.text
    summary = remote.get('/api/servers/71/private-key').json()
    assert summary == body
    with SessionLocal() as db:
        stored = db.get(ServerPrivateKey, 71)
        assert stored.owner_id == 1 and 'PRIVATE KEY' not in stored.key_encrypted


def test_passphrase_unlocks_once_and_is_not_stored(remote):
    text = openssh(ed25519.Ed25519PrivateKey.generate(), 'correct horse')
    assert code(put_key(remote, text)) == 'private_key_passphrase_required'
    wrong = put_key(remote, text, 'wrong passphrase')
    assert wrong.status_code == 422 and code(wrong) == 'private_key_passphrase_invalid'
    assert put_key(remote, text, 'correct horse').status_code == 200
    with SessionLocal() as db:
        assert 'correct horse' not in db.get(ServerPrivateKey, 71).key_encrypted
    pem = legacy_pem(rsa.generate_private_key(65537, 2048), 'pem secret')
    assert code(put_key(remote, pem)) == 'private_key_passphrase_required'
    assert code(put_key(remote, pem, 'nope')) == 'private_key_passphrase_invalid'
    assert put_key(remote, pem, 'pem secret').status_code == 200


@pytest.mark.parametrize('text', [
    'not a key',
    'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl user@host',
    'PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: none\n',
])
def test_invalid_input_is_rejected_without_echo(remote, text):
    result = put_key(remote, text)
    assert result.status_code == 422 and code(result) == 'private_key_invalid'
    assert text not in result.text


def test_weak_rsa_and_oversized_input_rejected_without_echo(remote):
    weak = legacy_pem(rsa.generate_private_key(65537, 1024))
    assert code(put_key(remote, weak)) == 'private_key_unsupported'
    huge = 'SECRET' + 'A' * 20000
    result = put_key(remote, huge)
    assert result.status_code == 422 and 'SECRET' not in result.text
    long_passphrase = put_key(remote, openssh(ed25519.Ed25519PrivateKey.generate()), 'P' * 2000)
    assert long_passphrase.status_code == 422 and 'PPPP' not in long_passphrase.text
    for body in ({'passphrase': 'ECHO-ME'}, {'private_key': ['ECHO-ME'], 'passphrase': 'ECHO-ME'},
                 {'private_key': 'x', 'passphrase': ['ECHO-ME']}):
        malformed = remote.put('/api/servers/71/private-key', json=body)
        assert malformed.status_code == 422 and 'ECHO-ME' not in malformed.text
    with SessionLocal() as db:
        assert db.get(ServerPrivateKey, 71) is None


def test_other_owner_cannot_read_set_or_delete(remote):
    text = openssh(ed25519.Ed25519PrivateKey.generate())
    assert put_key(remote, text, server=72).status_code == 404
    assert remote.get('/api/servers/72/private-key').status_code == 404
    assert remote.delete('/api/servers/72/private-key').status_code == 404
    assert put_key(remote, text).status_code == 200
    remote.headers['Authorization'] = 'Bearer ' + _create_token('other')
    assert remote.get('/api/servers/71/private-key').status_code == 404
    assert remote.delete('/api/servers/71/private-key').status_code == 404


def test_key_mode_authenticates_only_after_pinned_host(remote, network, auth_events):
    key = ed25519.Ed25519PrivateKey.generate()
    assert put_key(remote, openssh(key)).status_code == 200
    pin(remote, 'key')
    assert auth_events == ['handshake']
    assert remote.get('/api/servers/71/connection').json()['auth_mode'] == 'key'
    with SessionLocal() as db:
        with network.open_remote(db, db.get(User, 1), 71): pass
    assert ('publickey', 'runner', 'ssh-ed25519') == auth_events[-1][:3]
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH).decode()
    assert public.split()[1] == auth_events[-1][3]
    import paramiko
    FakeTransport.key = paramiko.RSAKey.generate(1024)
    changed = remote.post('/api/servers/71/connection/check')
    assert code(changed) == 'host_key_changed'
    assert auth_events[-1] == 'handshake'


def test_key_mode_without_usable_key_fails_clearly(remote, network, auth_events):
    pin(remote, 'key')
    missing = remote.post('/api/servers/71/connection/check')
    assert missing.status_code == 503 and code(missing) == 'private_key_required'
    assert put_key(remote, openssh(ed25519.Ed25519PrivateKey.generate())).status_code == 200
    with SessionLocal() as db:
        # Same effect as SECRET_KEY rotation: the ciphertext no longer decrypts.
        db.get(ServerPrivateKey, 71).key_encrypted = 'gAAAAAinvalid'
        db.commit()
    broken = remote.post('/api/servers/71/connection/check')
    assert broken.status_code == 503 and code(broken) == 'private_key_unreadable'
    assert remote.delete('/api/servers/71/private-key').json() == {'configured': False}
    assert code(remote.post('/api/servers/71/connection/check')) == 'private_key_required'
    assert not any(isinstance(e, tuple) and e[0] == 'publickey' for e in auth_events)


def test_deleting_server_removes_its_key(remote):
    assert put_key(remote, openssh(ed25519.Ed25519PrivateKey.generate())).status_code == 200
    assert remote.delete('/api/servers/71').status_code == 200
    with SessionLocal() as db:
        assert db.get(ServerPrivateKey, 71) is None


def test_review_edge_cases_return_sanitized_422(remote):
    # secp256k1 loads in cryptography but has no OpenSSH encoding.
    k1 = legacy_pem(ec.generate_private_key(ec.SECP256K1()))
    assert code(put_key(remote, k1)) == 'private_key_unsupported'
    # A non-object body must not be echoed by request validation.
    text = openssh(ed25519.Ed25519PrivateKey.generate())
    array = remote.put('/api/servers/71/private-key', json=[text])
    assert array.status_code == 422 and 'PRIVATE KEY' not in array.text
    # A damaged key is reported as damaged even when a passphrase is supplied.
    lines = openssh(ed25519.Ed25519PrivateKey.generate(), 'secret').splitlines()
    damaged = '\n'.join(lines[:2] + lines[3:]) + '\n'
    assert code(put_key(remote, damaged, 'secret')) == 'private_key_invalid'
    assert code(put_key(remote, openssh(ed25519.Ed25519PrivateKey.generate(), 'secret'), 'wrong')) == 'private_key_passphrase_invalid'
