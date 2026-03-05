"""用应用 secret_key 派生密钥，对远程连接密码做加密存储。"""
import base64
from typing import Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


def _get_fernet(secret_key: str) -> Fernet:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"panel_remote_monitor",
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(secret_key.encode("utf-8")))
    return Fernet(key)


def encrypt_password(secret_key: str, password: str) -> str:
    return _get_fernet(secret_key).encrypt(password.encode("utf-8")).decode("ascii")


def decrypt_password(secret_key: str, encrypted: str) -> Optional[str]:
    try:
        return _get_fernet(secret_key).decrypt(encrypted.encode("ascii")).decode("utf-8")
    except Exception:
        return None
