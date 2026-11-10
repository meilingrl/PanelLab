"""Secrets file (SMTP password, per-job secret env values), mode 0600.

Kept out of SQLite on purpose. Writes go to a temp file in the same directory
and are moved into place with os.replace.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from typing import Any, Dict, List, Optional

from .store import StoreError

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class Secrets:
    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.Lock()

    def get(self) -> Dict[str, Any]:
        """The whole secrets document: {"smtp_password": ..., "jobs": {id: {NAME: value}}}."""
        with self._lock:
            return self._load()

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise StoreError("invalid", "secrets file is corrupt")
        return data

    def _save(self, data: Dict[str, Any]) -> None:
        directory = os.path.dirname(os.path.abspath(self._path))
        fd, tmp = tempfile.mkstemp(prefix=".secrets-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                os.fchmod(f.fileno(), 0o600)
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def set_job_secrets(self, job_id: str, changes: Dict[str, Optional[str]]) -> None:
        """Value None deletes a name; names absent from `changes` stay as they are."""
        for k, v in changes.items():
            if not isinstance(k, str) or not _NAME_RE.fullmatch(k):
                raise StoreError("invalid", "bad secret name: %r" % (k,))
            if v is not None and not isinstance(v, str):
                raise StoreError("invalid", "secret values must be strings")
        with self._lock:
            data = self._load()
            jobs = data.setdefault("jobs", {})
            current = jobs.get(job_id, {})
            for k, v in changes.items():
                if v is None:
                    current.pop(k, None)
                else:
                    current[k] = v
            if current:
                jobs[job_id] = current
            else:
                jobs.pop(job_id, None)
            self._save(data)

    def job_secret_names(self, job_id: str) -> List[str]:
        return sorted(self.job_env(job_id))

    def job_env(self, job_id: str) -> Dict[str, str]:
        with self._lock:
            return dict(self._load().get("jobs", {}).get(job_id, {}))

    def set_smtp_password(self, value: Optional[str]) -> None:
        with self._lock:
            data = self._load()
            if value is None:
                data.pop("smtp_password", None)
            else:
                data["smtp_password"] = value
            self._save(data)

    def smtp_password(self) -> Optional[str]:
        with self._lock:
            return self._load().get("smtp_password")
