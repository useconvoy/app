"""One foreground child, one private directory, one exclusive owner (local-host extra).

Known launchers supply argv containing the generated marker as an *entire* argument.
No shells, daemonization, descendant management, or process-group signals. Call stop()
explicitly; closing the owner only releases its lock, so a successor can recover.
The directory and same-user callers are trusted; this is not a same-user sandbox.
"""

from __future__ import annotations

import fcntl
import functools
import json
import math
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Mapping, Sequence


class OwnershipError(RuntimeError):
    """Recovery needs attention; retained records must not be manually ignored."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _psutil():
    # Importing convoy-agent, including this module, keeps its stdlib-only contract.
    import psutil

    if psutil.__version__ != "7.2.2" or sys.platform not in ("darwin", "linux"):
        raise OwnershipError("unsupported_identity_backend")
    return psutil


def _write(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, allow_nan=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _serialized(method):
    @functools.wraps(method)
    def call(self, *args, **kwargs):
        with self._mutex:
            return method(self, *args, **kwargs)

    return call


class OwnedProcess:
    """Hold this context for the owner's lifetime; all operations are serialized.

    start() never replaces unresolved state. recover() terminates an exactly proven
    orphan; it never adopts it as healthy. A prelaunch intent with no discoverable
    marker stays blocked: absence from a process listing is not proof of no spawn.
    """

    def __init__(self, directory: Path):
        self.directory = Path(directory).absolute()
        self.record_path = self.directory / "process.json"
        self._lock: int | None = None
        self._owner_pid: int | None = None
        self._child: subprocess.Popen | None = None
        self._child_delivered = False
        self._mutex = threading.RLock()

    @_serialized
    def __enter__(self):
        if self._lock is not None:
            raise OwnershipError("already_locked")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise OwnershipError("unsafe_owner_directory")
        fd = os.open(self.directory / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise OwnershipError("owner_busy") from None
        self._lock, self._owner_pid = fd, os.getpid()
        return self

    @_serialized
    def __exit__(self, *_):
        if self._lock is not None:
            # A forked child must not unlock its parent's shared lock description.
            if self._owner_pid == os.getpid():
                fcntl.flock(self._lock, fcntl.LOCK_UN)
            os.close(self._lock)
            self._lock = None

    def _held(self):
        if self._lock is None or self._owner_pid != os.getpid():
            raise OwnershipError("owner_lock_required")

    def _blocked(self, code: str):
        # Keep original process.json, including malformed evidence, untouched.
        _write(self.directory / "failure.json", {"code": code, "observed_at": time.time()})
        raise OwnershipError(code)

    def _read(self) -> dict | None:
        self._held()
        try:
            info = self.record_path.lstat()
        except FileNotFoundError:
            return None
        try:
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError
            if info.st_size > 8192:
                raise ValueError
            record = json.loads(self.record_path.read_text())
            marker = Path(record["marker"])
            if (
                type(record["version"]) is not int
                or record["version"] != 1
                or type(record["uid"]) is not int
                or record["uid"] != os.getuid()
                or record["state"] not in ("intent", "running", "stopped")
                or marker.parent != self.directory
                or not marker.name.startswith("launch-")
                or len(marker.name) != 39
                or any(c not in "0123456789abcdef" for c in marker.name[7:])
                or not Path(record["exe"]).is_absolute()
            ):
                raise ValueError
            if record["state"] == "running" or record.get("pid") is not None:
                if type(record["pid"]) is not int or record["pid"] <= 1:
                    raise ValueError
                if type(record["created"]) not in (int, float) or not math.isfinite(record["created"]):
                    raise ValueError
            return record
        except (ValueError, TypeError, KeyError, OSError):
            self._blocked("invalid_record")

    @staticmethod
    def _created(process) -> float:
        # Pinned psutil7.2.2: _ident uses the kernel's unadjusted creation timestamp
        # on Darwin/Linux. Public create_time() may be adjusted after clock changes.
        value = process._ident[1]
        if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
            raise OwnershipError("creation_identity_unavailable")
        return value

    def _verify(self, process, record: dict, *, recorded: bool):
        psutil = _psutil()
        try:
            if recorded and self._created(process) != record["created"]:
                self._blocked("creation_identity_mismatch")
            if tuple(process.uids()) != (record["uid"],) * 3:
                self._blocked("user_identity_mismatch")
            # Public Process.exe() can guess from argv[0] after AccessDenied.
            # This version-pinned native call must supply an OS-observed path.
            executable = process._proc.exe()
            if not executable:
                self._blocked("executable_identity_unavailable")
            if os.path.realpath(executable) != record["exe"]:
                self._blocked("executable_identity_mismatch")
            if process.cmdline().count(record["marker"]) != 1:
                self._blocked("argument_marker_mismatch")
            if not process.is_running():
                self._blocked("identity_changed_during_inspection")
        except psutil.AccessDenied:
            self._blocked("identity_access_denied")

    @_serialized
    def start(
        self,
        argv_for_marker: Callable[[str], Sequence[str]],
        *,
        env: Mapping[str, str] | None = None,
        cwd: Path | None = None,
        stdout=None,
        stderr=None,
    ) -> subprocess.Popen:
        """Launch a trusted foreground executable; caller owns output draining.

        argv[0] must name an absolute binary (use the Python binary for modules).
        The generated 0600 marker file can also hold a launcher's private API key.
        """
        self._held()
        _psutil()
        old = self._read()
        if old is not None and old["state"] != "stopped":
            self._blocked("unresolved_previous_launch")
        marker = self.directory / ("launch-" + secrets.token_hex(16))
        marker.touch(mode=0o600, exist_ok=False)
        argv = list(argv_for_marker(str(marker)))
        if not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
            raise ValueError("argv must be nonempty strings")
        if not Path(argv[0]).is_absolute() or argv.count(str(marker)) != 1:
            raise ValueError("absolute executable and exactly one complete marker argument required")
        record = {
            "version": 1,
            "state": "intent",
            "marker": str(marker),
            "uid": os.getuid(),
            "exe": os.path.realpath(argv[0]),
            "pid": None,
        }
        if old is not None:
            _write(self.directory / "previous.json", old)
            Path(old["marker"]).unlink(missing_ok=True)
        _write(self.record_path, record)  # durable BEFORE Popen (including its fork/exec gap)
        try:
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL if stdout is None else stdout,
                stderr=subprocess.DEVNULL if stderr is None else stderr,
                close_fds=True,
                start_new_session=True,
            )
        except OSError:
            # A failed Popen reaps its own failed exec child. Retain the launch evidence.
            record.update(state="stopped", result="spawn_failed")
            _write(self.record_path, record)
            raise
        self._child = child
        self._child_delivered = False
        try:
            process = _psutil().Process(child.pid)
            self._verify(process, record, recorded=False)
            record.update(state="running", pid=child.pid, created=self._created(process))
            _write(self.record_path, record)
        except (_psutil().Error, OwnershipError) as error:
            # The still-live parent can prove its exact child's exit with waitpid.
            # A successor with only an intent cannot make this inference.
            try:
                exit_code = child.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                if isinstance(error, OwnershipError):
                    raise
                self._blocked("child_identity_unavailable")
            self._close_undelivered_output()
            record.update(state="stopped", result="exited_during_launch", exit_code=exit_code)
            _write(self.record_path, record)
            raise OwnershipError("child_exited_during_launch") from error
        self._child_delivered = True
        return child

    def _close_undelivered_output(self):
        if self._child is not None and not self._child_delivered:
            for stream in (self._child.stdout, self._child.stderr):
                if stream is not None:
                    stream.close()

    def _find(self, record: dict):
        psutil = _psutil()
        if record.get("pid") is not None:
            try:
                process = psutil.Process(record["pid"])
                # Even a recycled PID must block, never be treated as an absent child.
                if self._created(process) != record["created"]:
                    self._blocked("creation_identity_mismatch")
                if process.status() == psutil.STATUS_ZOMBIE:
                    return None  # this exact incarnation can no longer execute
                self._verify(process, record, recorded=True)
                return process
            except psutil.NoSuchProcess:
                if self._exited(record):
                    return None
                self._blocked("identity_temporarily_unavailable")
            except psutil.AccessDenied:
                self._blocked("identity_access_denied")
        matches = []
        # Avoid process_iter's identity cache. Never inspect or signal by process name.
        for pid in psutil.pids():
            try:
                process = psutil.Process(pid)
                if record["uid"] not in process.uids():
                    continue
                if record["marker"] in process.cmdline():
                    self._verify(process, record, recorded=False)
                    matches.append(process)
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except psutil.AccessDenied:
                # Unreadable candidates cannot justify "nothing is running". A unique
                # positive exact match is sufficient for this single-child launcher.
                continue
        if len(matches) != 1:
            self._blocked("intent_child_not_proven" if not matches else "ambiguous_marker")
        process = matches[0]
        record.update(state="running", pid=process.pid, created=self._created(process))
        _write(self.record_path, record)
        return process

    def _exited(self, record: dict) -> bool:
        # On Darwin exe()/cmdline() can report NoSuchProcess while a dying PID still
        # exists in state "idle". That exception alone is NOT verified process exit.
        psutil = _psutil()
        try:
            process = psutil.Process(record["pid"])
            if self._created(process) != record["created"]:
                self._blocked("creation_identity_mismatch")
            return process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return not psutil.pid_exists(record["pid"])
        except psutil.AccessDenied:
            self._blocked("identity_access_denied")

    @_serialized
    def stop(self, *, terminate_timeout: float = 3.0, kill_timeout: float = 3.0) -> dict:
        """Bounded TERM then KILL of one reverified identity; preserve unresolved state."""
        self._held()
        if any(not math.isfinite(t) or not 0 <= t <= 30 for t in (terminate_timeout, kill_timeout)):
            raise ValueError("timeouts must be between 0 and 30 seconds")
        record = self._read()
        if record is None or record["state"] == "stopped":
            return {"action": "none"}
        psutil = _psutil()
        process = self._find(record)
        for action, timeout in (("terminate", terminate_timeout), ("kill", kill_timeout)):
            if process is None:
                break
            try:
                self._verify(process, record, recorded=True)
                getattr(process, action)()  # psutil rechecks PID creation identity before os.kill
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if self._exited(record):
                        process = None
                        break
                    time.sleep(min(0.025, max(0, deadline - time.monotonic())))
            except psutil.NoSuchProcess:
                if not self._exited(record):
                    self._blocked("identity_temporarily_unavailable")
                process = None
            except psutil.AccessDenied:
                self._blocked("signal_access_denied")
        if process is not None and not self._exited(record):
            self._blocked("exit_not_verified")
        if self._child is not None and self._child.pid == record.get("pid"):
            self._child.poll()  # reap our direct child; recovered orphans are reaped by init
            self._close_undelivered_output()
        record.update(state="stopped", result="verified_exit")
        _write(self.record_path, record)
        return {"action": "stopped", "pid": record.get("pid")}

    def recover(self, **timeouts) -> dict:
        """A successor stops the previous child before any new start; no adoption."""
        return self.stop(**timeouts)
