"""Accept a client private key once, keep it encrypted, and load it only for pinned logins."""
import io

import paramiko
from fastapi import HTTPException
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, rsa

from config import get_settings
from models.server_key import ServerPrivateKey
from services.remote_connection import fail, fingerprint, owned_server
from utils.cipher import decrypt_password, encrypt_password

KEY_TEXT_LIMIT = 16 * 1024
PASSPHRASE_LIMIT = 1024
MIN_RSA_BITS = 2048
PARAMIKO_TYPES = ((ed25519.Ed25519PrivateKey, paramiko.Ed25519Key),
                  (ec.EllipticCurvePrivateKey, paramiko.ECDSAKey),
                  (rsa.RSAPrivateKey, paramiko.RSAKey))


def invalid(code, message):
    # Never include the submitted key or passphrase in an error.
    fail(code, message, 422)


def _decode(text, passphrase):
    data = text.strip().encode('utf-8') + b'\n'
    password = passphrase.encode('utf-8') if passphrase else None
    load = serialization.load_ssh_private_key if b'BEGIN OPENSSH PRIVATE KEY' in data else serialization.load_pem_private_key
    try:
        return load(data, password=password)
    except TypeError:
        # cryptography reports a missing passphrase, or one given for a plain key, as TypeError.
        if password is None: invalid('private_key_passphrase_required', 'This private key is encrypted; enter its passphrase')
        return load(data, password=None)


def parse_private_key(text, passphrase=None):
    """Return (unencrypted OpenSSH text, paramiko key). Raises sanitized 422 errors."""
    if not isinstance(text, str) or not text.strip() or len(text) > KEY_TEXT_LIMIT:
        invalid('private_key_invalid', 'Paste an OpenSSH or PEM private key no larger than 16 KiB')
    if passphrase is not None and (not isinstance(passphrase, str) or len(passphrase) > PASSPHRASE_LIMIT):
        invalid('private_key_passphrase_invalid', 'The passphrase is too long')
    if 'BEGIN' not in text or 'PRIVATE KEY' not in text:
        invalid('private_key_invalid', 'Paste an OpenSSH or PEM private key, not a public key or PuTTY file')
    try:
        key = _decode(text, passphrase or None)
    except HTTPException:
        raise
    except Exception:
        if passphrase:
            invalid('private_key_passphrase_invalid', 'The passphrase does not unlock this private key')
        invalid('private_key_invalid', 'The private key could not be read')
    kind = next((cls for base, cls in PARAMIKO_TYPES if isinstance(key, base)), None)
    if kind is None:
        invalid('private_key_unsupported', 'Use an Ed25519, ECDSA or RSA (2048-bit or larger) key')
    if isinstance(key, rsa.RSAPrivateKey) and key.key_size < MIN_RSA_BITS:
        invalid('private_key_unsupported', 'RSA keys must be at least 2048 bits')
    normalized = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                   serialization.NoEncryption()).decode('ascii')
    return normalized, kind.from_private_key(io.StringIO(normalized))


def key_class(key_type):
    if key_type == 'ssh-ed25519': return paramiko.Ed25519Key
    if key_type.startswith('ecdsa-sha2-'): return paramiko.ECDSAKey
    if key_type == 'ssh-rsa': return paramiko.RSAKey
    raise ValueError('unsupported key type')


def summary(row):
    if row is None: return {'configured': False}
    return {'configured': True, 'key_type': row.key_type, 'fingerprint': row.fingerprint}


def get_summary(db, user, server_id):
    owned_server(db, user, server_id)
    row = db.get(ServerPrivateKey, server_id)
    return summary(row if row and row.owner_id == user.id else None)


def save(db, user, server_id, text, passphrase=None):
    server = owned_server(db, user, server_id)
    normalized, key = parse_private_key(text, passphrase)
    row = db.get(ServerPrivateKey, server.id)
    if row is None:
        row = ServerPrivateKey(server_id=server.id)
        db.add(row)
    row.owner_id = user.id
    row.key_encrypted = encrypt_password(get_settings().secret_key, normalized)
    row.key_type = key.get_name()
    row.fingerprint = fingerprint(key)
    db.commit()
    return summary(row)


def delete(db, user, server_id):
    owned_server(db, user, server_id)
    db.query(ServerPrivateKey).filter(ServerPrivateKey.server_id == server_id).delete()
    db.commit()
    return {'configured': False}


def load(db, server):
    row = db.get(ServerPrivateKey, server.id)
    if row is None or row.owner_id != server.user_id:
        fail('private_key_required', 'Upload a private key for this server first', 503)
    text = decrypt_password(get_settings().secret_key, row.key_encrypted)
    if not text:
        fail('private_key_unreadable', 'The stored private key cannot be decrypted; upload it again', 503)
    try:
        return key_class(row.key_type).from_private_key(io.StringIO(text))
    except Exception:
        fail('private_key_unreadable', 'The stored private key cannot be loaded; upload it again', 503)
