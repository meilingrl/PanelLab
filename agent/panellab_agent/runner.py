"""Job runner: spawns commands, enforces timeout/output cap, handles retries.

Thread-safety: all mutable state is guarded by one RLock. `poll()` is meant
to be called about once a second from the daemon main loop; `start()`,
`read_output()` and `cancel()` may be called from other threads. `on_finish`
callbacks run from `poll()` *outside* the lock.
"""
from __future__ import annotations

import os
import re
import select
import signal
import subprocess
import sys
import threading
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set

from .clock import Clock
from .secret_store import Secrets
from .store import Store, StoreError

TRUNCATION_MARKER = "[输出超过 10 MiB，已截断]"
DEFAULT_OUTPUT_LIMIT = 10 * 1024 * 1024
ABANDON_MARKER = "[作业结束后仍有后台进程占用输出，已停止收集]"
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BASE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}


class _Active:
    def __init__(self, job: Dict[str, Any], trigger: str, scheduled_for: Optional[datetime],
                 attempt: int, run_id: int, proc: "subprocess.Popen", started: float) -> None:
        self.job = job
        self.trigger = trigger
        self.scheduled_for = scheduled_for
        self.attempt = attempt
        self.run_id = run_id
        self.proc = proc
        self.deadline = started + float(job["timeout_seconds"])
        self.timed_out = False
        self.cancelled = False
        self.term_at: Optional[float] = None
        self.killed = False
        self.kill_at: Optional[float] = None
        self.abandon = False
        self.exited_at: Optional[float] = None
        self.reader_done = threading.Event()
        # written by the reader thread, read after reader_done is set
        self.written = 0
        self.truncated = False


class _Retry:
    def __init__(self, job: Dict[str, Any], attempt: int, due: float, trigger: str,
                 scheduled_for: Optional[datetime]) -> None:
        self.job = job
        self.attempt = attempt
        self.due = due
        self.trigger = trigger
        self.scheduled_for = scheduled_for


class _EnvFileError(Exception):
    pass


def _parse_env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    with open(path, "r", encoding="utf-8-sig") as f:
        for n, raw in enumerate(f, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export ") or line.startswith("export\t"):
                line = line[7:].lstrip()
            key, sep, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not sep or not _KEY_RE.match(key):
                raise _EnvFileError("环境变量文件第 %d 行格式不正确" % n)
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            out[key] = value
    return out


class Runner:
    def __init__(self, store: Store, secrets: Secrets, clock: Clock, output_dir: str,
                 on_finish: Callable[[Dict[str, Any], Dict[str, Any]], None],
                 kill_grace_seconds: float = 30,
                 output_limit: int = DEFAULT_OUTPUT_LIMIT) -> None:
        self._store = store
        self._secrets = secrets
        self._clock = clock
        self._dir = output_dir
        self._on_finish = on_finish
        self._grace = kill_grace_seconds
        self._limit = output_limit
        self._lock = threading.RLock()
        self._active: Dict[int, _Active] = {}
        self._retries: List[_Retry] = []
        # (job, trigger, scheduled_for, attempt, run_id) finished by us, awaiting chain decision
        self._done: List[Any] = []
        os.makedirs(output_dir, exist_ok=True)

    # ---- helpers ----------------------------------------------------------

    def _path(self, run_id: int) -> str:
        return os.path.join(self._dir, "%d.log" % int(run_id))

    def _build_env(self, job: Dict[str, Any]) -> Dict[str, str]:
        env = dict(_BASE_ENV)
        env["HOME"] = job["workdir"]
        if job.get("env_file"):
            env.update(_parse_env_file(job["env_file"]))
        env.update({str(k): str(v) for k, v in (job.get("env") or {}).items()})
        env.update(self._secrets.job_env(job["id"]))
        return env

    def _fail_start(self, job, trigger, scheduled_for, attempt, run_id, reason: str) -> None:
        text = ("无法启动：" + reason + "\n").encode("utf-8")
        size = len(text)
        try:
            with open(self._path(run_id), "wb") as f:
                f.write(text)
        except OSError as e:
            print("runner: cannot write output for run %s: %s" % (run_id, e), file=sys.stderr)
            size = 0
        self._store.finish_run(run_id, "failed", None, size, False)
        self._done.append((job, trigger, scheduled_for, attempt, run_id))

    def _reader(self, act: _Active, out) -> None:
        stream = act.proc.stdout
        fd = stream.fileno()
        last = b"\n"
        try:
            while True:
                if act.abandon:
                    tail = (b"" if last == b"\n" else b"\n") + \
                        ABANDON_MARKER.encode("utf-8") + b"\n"
                    out.write(tail)
                    act.written += len(tail)
                    break
                ready, _, _ = select.select([fd], [], [], 0.2)
                if not ready:
                    continue
                chunk = os.read(fd, 65536)
                if not chunk:
                    break
                if act.truncated:
                    continue  # keep draining so the child never blocks
                room = self._limit - act.written
                if len(chunk) > room:
                    chunk = chunk[:max(room, 0)]
                    if chunk:
                        out.write(chunk)
                        act.written += len(chunk)
                        last = chunk[-1:]
                    tail = (b"" if last == b"\n" else b"\n") + TRUNCATION_MARKER.encode("utf-8") + b"\n"
                    out.write(tail)
                    act.written += len(tail)
                    act.truncated = True
                else:
                    out.write(chunk)
                    act.written += len(chunk)
                    last = chunk[-1:]
        except Exception as e:  # never let the reader thread die noisily
            print("runner: output reader error: %s" % e, file=sys.stderr)
        finally:
            try:
                out.close()
                stream.close()
            except Exception:
                pass
            act.reader_done.set()

    @staticmethod
    def _signal_group(act: _Active, sig: int) -> None:
        try:
            os.killpg(act.proc.pid, sig)
        except OSError:
            pass

    # ---- public API -------------------------------------------------------

    def start(self, job: Dict[str, Any], trigger: str, scheduled_for: Optional[datetime],
              attempt: int = 1) -> int:
        with self._lock:
            run_id = self._store.insert_run(job["id"], trigger, scheduled_for, attempt)
            workdir = job["workdir"]
            if not os.path.isdir(workdir):
                self._fail_start(job, trigger, scheduled_for, attempt, run_id,
                                 "工作目录不存在 %s" % workdir)
                return run_id
            try:
                env = self._build_env(job)
            except _EnvFileError as e:
                self._fail_start(job, trigger, scheduled_for, attempt, run_id, str(e))
                return run_id
            except (OSError, UnicodeDecodeError, ValueError):
                self._fail_start(job, trigger, scheduled_for, attempt, run_id,
                                 "无法读取环境变量文件 %s" % job.get("env_file"))
                return run_id
            try:
                out = open(self._path(run_id), "wb", buffering=0)
            except OSError as e:
                self._fail_start(job, trigger, scheduled_for, attempt, run_id,
                                 "无法创建输出文件（%s）" % (e.strerror or type(e).__name__))
                return run_id
            try:
                proc = subprocess.Popen(job["command"], cwd=workdir, env=env,
                                        start_new_session=True, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        bufsize=0)
            except FileNotFoundError:
                out.close()
                self._fail_start(job, trigger, scheduled_for, attempt, run_id,
                                 "找不到命令 %s" % job["command"][0])
                return run_id
            except PermissionError:
                out.close()
                self._fail_start(job, trigger, scheduled_for, attempt, run_id,
                                 "没有权限执行 %s" % job["command"][0])
                return run_id
            except (OSError, ValueError) as e:
                out.close()
                self._fail_start(job, trigger, scheduled_for, attempt, run_id,
                                 "%s" % (getattr(e, "strerror", None) or type(e).__name__))
                return run_id
            act = _Active(job, trigger, scheduled_for, attempt, run_id, proc,
                          self._clock.monotonic())
            try:
                threading.Thread(target=self._reader, args=(act, out), daemon=True,
                                 name="runner-out-%d" % run_id).start()
            except Exception as e:
                self._signal_group(act, signal.SIGKILL)
                proc.wait()
                proc.stdout.close()
                out.close()
                self._fail_start(job, trigger, scheduled_for, attempt, run_id,
                                 "无法启动输出线程（%s）" % type(e).__name__)
                return run_id
            self._active[run_id] = act
            return run_id

    def cancel(self, run_id: int) -> bool:
        """Request cancellation. Returns False when the run is not active here."""
        with self._lock:
            act = self._active.get(run_id)
            if act is None or act.cancelled:
                return False
            act.cancelled = True
            if act.term_at is None and act.proc.poll() is None:
                act.term_at = self._clock.monotonic()
                self._signal_group(act, signal.SIGTERM)
            return True

    def running_job_ids(self) -> Set[str]:
        with self._lock:
            ids = {a.job["id"] for a in self._active.values()}
            ids.update(r.job["id"] for r in self._retries)
            ids.update(d[0]["id"] for d in self._done)
            return ids

    def drop_retries(self, job_id: Optional[str] = None) -> int:
        """Forget pending retries for one job (or all jobs when None); return how many."""
        with self._lock:
            keep = [r for r in self._retries
                    if job_id is not None and r.job["id"] != job_id]
            dropped = len(self._retries) - len(keep)
            self._retries = keep
            return dropped

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def _poll_active(self, act: _Active, now: float) -> bool:
        """Advance one active attempt; return True when it was finalized."""
        rc = act.proc.poll()
        if rc is None:
            if act.term_at is None and now >= act.deadline:
                act.timed_out = True
                act.term_at = now
                self._signal_group(act, signal.SIGTERM)
            elif act.term_at is not None and not act.killed \
                    and now - act.term_at >= self._grace:
                act.killed = True
                act.kill_at = now
                self._signal_group(act, signal.SIGKILL)
            return False
        if act.exited_at is None:
            act.exited_at = now
        if not act.reader_done.is_set():
            # Something still holds the pipe open (grandchild). Kill the group after
            # the grace, then stop waiting for EOF after another grace.
            if not act.killed:
                if now - act.exited_at >= self._grace:
                    act.killed = True
                    act.kill_at = now
                    self._signal_group(act, signal.SIGKILL)
            elif now - (act.kill_at or now) >= self._grace:
                act.abandon = True
            return False
        self._finalize(act, rc)
        return True

    def poll(self) -> None:
        callbacks: List[Any] = []
        with self._lock:
            now = self._clock.monotonic()
            for run_id, act in list(self._active.items()):
                try:
                    if self._poll_active(act, now):
                        del self._active[run_id]
                except Exception as e:
                    print("runner: polling run %s failed: %r" % (run_id, e), file=sys.stderr)
            # drain finished attempts: decide retry vs final report
            done, self._done = self._done, []
            for entry in done:
                job, trigger, scheduled_for, attempt, run_id = entry
                try:
                    run = self._store.get_run(run_id)
                    if run is not None and run["status"] in ("failed", "timed_out") \
                            and attempt <= int(job["retries"]):
                        self._retries.append(_Retry(
                            job, attempt + 1, now + float(job["retry_delay_seconds"]),
                            trigger, scheduled_for))
                    else:
                        callbacks.append((job, run))
                except Exception as e:
                    print("runner: finishing run %s failed: %r" % (run_id, e), file=sys.stderr)
                    self._done.append(entry)  # retry on the next poll
            due = [r for r in self._retries if r.due <= now]
            self._retries = [r for r in self._retries if r.due > now]
            for r in due:
                try:
                    self.start(r.job, "retry", r.scheduled_for, r.attempt)
                except Exception as e:  # e.g. job deleted meanwhile; give up on this chain
                    print("runner: retry of %s abandoned: %r" % (r.job["id"], e), file=sys.stderr)
        for job, run in callbacks:
            try:
                self._on_finish(job, run)
            except Exception as e:
                print("runner: on_finish failed for %s: %r" % (job.get("id"), e), file=sys.stderr)

    def _finalize(self, act: _Active, rc: int) -> None:
        run = self._store.get_run(act.run_id)
        if run is None or run["status"] != "running":
            return  # someone else already finished it; do not overwrite
        if act.cancelled:
            status = "cancelled"
        elif act.timed_out:
            status = "timed_out"
        elif rc == 0:
            status = "succeeded"
        else:
            status = "failed"
        self._store.finish_run(act.run_id, status, rc, act.written, act.truncated)
        self._done.append((act.job, act.trigger, act.scheduled_for, act.attempt, act.run_id))

    def read_output(self, run_id: int, offset: int, limit: int = 65536) -> Dict[str, Any]:
        with self._lock:
            run = self._store.get_run(run_id)
            if run is None:
                raise StoreError("not_found", "no such run: %s" % run_id)
            act = self._active.get(run_id)
            running = run["status"] == "running"
            truncated = bool(run["truncated"]) or (act is not None and act.truncated)
        offset = max(int(offset), 0)
        try:
            with open(self._path(run_id), "rb") as f:
                size = os.fstat(f.fileno()).st_size
                f.seek(offset)
                data = f.read(max(int(limit), 0))
        except FileNotFoundError:
            size, data = 0, b""
        if offset + len(data) < size and data:
            # Not at EOF: back off up to 3 bytes so we never split a UTF-8 sequence.
            for i in range(1, min(3, len(data)) + 1):
                b = data[-i]
                if b & 0xC0 == 0x80:
                    continue
                need = 1 if b < 0x80 else 2 if b < 0xE0 else 3 if b < 0xF0 else 4
                if need > i and len(data) > i:
                    data = data[:-i]
                break
        new_offset = offset + len(data)
        return {"data": data.decode("utf-8", errors="replace"), "offset": new_offset,
                "eof": (not running) and new_offset >= size, "truncated": truncated}

    def delete_output(self, run_ids: List[int]) -> None:
        for rid in run_ids:
            try:
                os.remove(self._path(rid))
            except OSError:
                pass
