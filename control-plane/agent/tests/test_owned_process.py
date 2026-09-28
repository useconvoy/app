"""Actual tiny foreground processes exercise the native ownership boundary."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import ExitStack
from pathlib import Path

import pytest
from convoy_agent.owned_process import OwnedProcess, OwnershipError

psutil = pytest.importorskip("psutil")

pytestmark = pytest.mark.skipif(
    sys.platform not in ("darwin", "linux") or psutil.__version__ != "7.2.2",
    reason="requires pinned convoy-agent[local-host] identity backend",
)

CHILD = "import time; time.sleep(120)"


@pytest.fixture(autouse=True)
def reap_test_children(monkeypatch):
    """A failed assertion/launch inspection must not leak this test's own children."""
    original = subprocess.Popen
    children = []

    def tracked(*args, **kwargs):
        child = original(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", tracked)
    yield
    for child in children:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)


def argv(marker):
    return [sys.executable, "-c", CHILD, marker]


def read(path):
    return json.loads((path / "process.json").read_text())


def write(path, record):
    (path / "process.json").write_text(json.dumps(record))


def wait_file(path, process=None):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process is not None and process.poll() is not None:
            raise AssertionError("test owner exited early")
        time.sleep(0.025)
    raise AssertionError("test child did not become ready")


def alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def test_normal_lifecycle_lock_and_kill_escalation(tmp_path):
    directory = tmp_path / "owner"
    with OwnedProcess(directory) as owner:
        child = owner.start(
            lambda marker: [
                sys.executable,
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "open(__import__('sys').argv[2],'w').close(); time.sleep(120)",
                marker,
                str(tmp_path / "ready"),
            ]
        )
        try:
            wait_file(tmp_path / "ready", child)
            record = read(directory)
            assert record["pid"] == child.pid
            assert record["created"] == psutil.Process(child.pid)._ident[1]
            assert record["exe"] == os.path.realpath(sys.executable)
            assert (directory / "process.json").stat().st_mode & 0o777 == 0o600
            assert Path(record["marker"]).stat().st_mode & 0o777 == 0o600
            with pytest.raises(OwnershipError, match="owner_busy"), OwnedProcess(directory):
                pass
            with pytest.raises(OwnershipError, match="unresolved_previous_launch"):
                owner.start(argv)
            t0 = time.monotonic()
            assert owner.stop(terminate_timeout=0.1, kill_timeout=2)["action"] == "stopped"
            assert time.monotonic() - t0 < 3
            assert child.poll() == -signal.SIGKILL
            assert read(directory)["result"] == "verified_exit"
            assert owner.stop()["action"] == "none"
            replacement = owner.start(argv)
            assert replacement.pid != child.pid
            owner.stop()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


@pytest.mark.parametrize("fault", ["malformed", "pid_reused", "marker", "exe", "uid"])
def test_unprovable_record_blocks_signal_and_replacement(tmp_path, fault):
    directory = tmp_path / "owner"
    with OwnedProcess(directory) as owner:
        child = owner.start(argv)
        original = read(directory)
        try:
            changed = dict(original)
            if fault == "malformed":
                (directory / "process.json").write_text("{broken")
            else:
                if fault == "pid_reused":
                    changed["created"] += 1
                elif fault == "marker":
                    changed["marker"] = str(directory / ("launch-" + "0" * 32))
                elif fault == "exe":
                    changed["exe"] = "/not-the-owned-binary"
                else:
                    changed["uid"] += 1
                write(directory, changed)
            evidence = (directory / "process.json").read_bytes()
            with pytest.raises(OwnershipError):
                owner.recover(terminate_timeout=0.1, kill_timeout=0.1)
            assert alive(child.pid)
            assert (directory / "process.json").read_bytes() == evidence
            assert (directory / "failure.json").exists()
            with pytest.raises(OwnershipError):
                owner.start(argv)
            assert alive(child.pid)
        finally:
            write(directory, original)
            owner.stop()


def test_missing_intent_child_stays_blocked(tmp_path):
    directory = tmp_path / "owner"
    with OwnedProcess(directory) as owner:
        child = owner.start(argv)
        record = read(directory)
        owner.stop()
        record.update(state="intent", pid=None)
        record.pop("created")
        write(directory, record)
        with pytest.raises(OwnershipError, match="intent_child_not_proven"):
            owner.recover()
        assert read(directory) == record
        with pytest.raises(OwnershipError, match="unresolved_previous_launch"):
            owner.start(argv)
        assert child.poll() is not None


@pytest.mark.parametrize("before_pid_save", [False, True])
def test_killed_owner_recovery_leaves_unrelated_process_untouched(tmp_path, before_pid_save):
    directory = tmp_path / "owner"
    # A different process with the same executable and script is not owned.
    sentinel = subprocess.Popen(argv("unrelated-sentinel"), start_new_session=True)
    owner_script = r"""
import os, sys, time, subprocess
from pathlib import Path
from convoy_agent.owned_process import OwnedProcess
root = Path(sys.argv[1])
real_popen = subprocess.Popen
if sys.argv[2] == "gap":
    def crash_gap(*a, **kw):
        child = real_popen(*a, **kw)
        (root.parent / "child-pid").write_text(str(child.pid))
        (root.parent / "ready").touch()
        time.sleep(120)  # killed here, before OwnedProcess can record the PID
        return child
    subprocess.Popen = crash_gap
with OwnedProcess(root) as owner:
    child = owner.start(lambda marker: [sys.executable, "-c", "import time; time.sleep(120)", marker])
    (root.parent / "child-pid").write_text(str(child.pid))
    (root.parent / "ready").touch()
    time.sleep(120)
"""
    agent_root = str(Path(__file__).resolve().parents[1])
    owner_process = subprocess.Popen(
        [sys.executable, "-c", owner_script, str(directory), "gap" if before_pid_save else "saved"],
        env={**os.environ, "PYTHONPATH": agent_root},
        start_new_session=True,
    )
    owned_pid = None
    owned_process = None
    owned_marker = None
    try:
        wait_file(tmp_path / "ready", owner_process)
        owned_pid = int((tmp_path / "child-pid").read_text())
        owned_process = psutil.Process(owned_pid)
        owned_marker = read(directory)["marker"]
        assert alive(owned_pid)
        with pytest.raises(OwnershipError, match="owner_busy"), OwnedProcess(directory):
            pass
        if before_pid_save:
            assert read(directory)["state"] == "intent" and read(directory)["pid"] is None
        owner_process.kill()
        owner_process.wait(timeout=3)
        with OwnedProcess(directory) as successor:
            assert successor.recover(terminate_timeout=2, kill_timeout=2)["pid"] == owned_pid
            assert not alive(owned_pid)
            assert sentinel.poll() is None
            replacement = successor.start(argv)
            successor.stop()
            assert replacement.poll() is not None
            assert (directory / "previous.json").exists()
        assert sentinel.poll() is None
    finally:
        if owner_process.poll() is None:
            owner_process.kill()
            owner_process.wait(timeout=3)
        # Test cleanup uses its own observed child identity, never process-name matching.
        if owned_process is not None and owned_process.is_running():
            try:
                assert owned_process.cmdline().count(owned_marker) == 1
                owned_process.kill()  # retained creation identity protects against PID reuse
            except psutil.NoSuchProcess:
                pass
        sentinel.terminate()
        sentinel.wait(timeout=3)


def test_access_denied_retains_record(tmp_path, monkeypatch):
    directory = tmp_path / "owner"
    with OwnedProcess(directory) as owner:
        child = owner.start(argv)
        before = (directory / "process.json").read_bytes()
        native_process = type(psutil.Process(child.pid)._proc)
        real_exe = native_process.exe

        def denied(process):
            if process.pid == child.pid:
                raise psutil.AccessDenied(child.pid)
            return real_exe(process)

        with monkeypatch.context() as patch:
            patch.setattr(native_process, "exe", denied)
            # Public psutil silently guesses argv[0]; the helper must not accept it.
            assert psutil.Process(child.pid).exe() == sys.executable
            with pytest.raises(OwnershipError, match="identity_access_denied"):
                owner.stop()
            assert child.poll() is None
            assert (directory / "process.json").read_bytes() == before
        owner.stop()


def test_ambiguous_intent_marker_does_not_signal_either_process(tmp_path):
    directory = tmp_path / "owner"
    with OwnedProcess(directory) as owner:
        child = owner.start(argv)
        original = read(directory)
        duplicate = subprocess.Popen(argv(original["marker"]), start_new_session=True)
        try:
            intent = {**original, "state": "intent", "pid": None}
            intent.pop("created")
            write(directory, intent)
            with pytest.raises(OwnershipError, match="ambiguous_marker"):
                owner.recover()
            assert child.poll() is None and duplicate.poll() is None
            assert read(directory) == intent
        finally:
            duplicate.terminate()
            duplicate.wait(timeout=3)
            write(directory, original)
            owner.stop()


@pytest.mark.parametrize("child_argv", [["-c", "raise SystemExit(0)"], ["--invalid-python-option"]])
def test_definite_early_child_exit_allows_replacement(tmp_path, monkeypatch, child_argv):
    directory = tmp_path / "owner"
    real_popen = subprocess.Popen
    streams = []

    def launch_and_observe_exit(*args, **kwargs):
        # Actual immediate exit/native argument failure, scheduled before identity save.
        child = real_popen(*args, **kwargs)
        streams.extend([child.stdout, child.stderr])
        child.wait(timeout=3)
        return child

    with OwnedProcess(directory) as owner:
        with monkeypatch.context() as patch:
            patch.setattr(subprocess, "Popen", launch_and_observe_exit)
            with pytest.raises(OwnershipError, match="child_exited_during_launch"):
                owner.start(
                    lambda marker: [sys.executable, *child_argv, marker],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
        assert all(stream.closed for stream in streams)
        assert read(directory)["state"] == "stopped"
        assert read(directory)["result"] == "exited_during_launch"
        assert read(directory)["exit_code"] == (0 if child_argv[0] == "-c" else 2)
        child = owner.start(argv)
        owner.stop()
        assert child.poll() is not None


@pytest.mark.skipif(sys.platform != "linux", reason="Linux leader/task-group exit semantics")
@pytest.mark.parametrize("recovered", [False, True])
def test_zombie_leader_does_not_publish_stopped_while_thread_owns_listener(tmp_path, recovered):
    code = r"""
import ctypes, json, socket, sys, threading, time
from pathlib import Path
root = Path(sys.argv[2])
listener = socket.socket()
listener.bind(("127.0.0.1", 0))
listener.listen(8)
def finish():
    deadline = time.monotonic() + 10
    while not (root / "finish").exists() and time.monotonic() < deadline:
        time.sleep(.01)
    listener.close()
threading.Thread(target=finish).start()
(root / "ready").write_text(json.dumps({"port": listener.getsockname()[1]}))
deadline = time.monotonic() + 5
while not (root / "go").exists() and time.monotonic() < deadline:
    time.sleep(.01)
if not (root / "go").exists():
    raise SystemExit(2)
pthread_exit = ctypes.CDLL(None).pthread_exit
pthread_exit.argtypes, pthread_exit.restype = [ctypes.c_void_p], None
pthread_exit(None)
"""
    directory = tmp_path / "owner"
    with ExitStack() as stack:
        owner = stack.enter_context(OwnedProcess(directory))
        child = owner.start(lambda marker: [sys.executable, "-c", code, marker, str(tmp_path)])
        wait_file(tmp_path / "ready", child)
        port = json.loads((tmp_path / "ready").read_text())["port"]
        process = psutil.Process(child.pid)
        assert read(directory)["created"] == process._ident[1]
        (tmp_path / "go").touch()
        deadline = time.monotonic() + 5
        while process.status() != psutil.STATUS_ZOMBIE:
            assert child.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        if recovered:
            stack.close()
            owner = stack.enter_context(OwnedProcess(directory))
            assert owner._child is None
        before = (directory / "process.json").read_bytes()
        with pytest.raises(OwnershipError, match="exit_not_verified"):
            owner.stop(terminate_timeout=.02, kill_timeout=.02)
        assert (directory / "process.json").read_bytes() == before
        assert child.poll() is None
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) == 0
        with pytest.raises(OwnershipError, match="unresolved_previous_launch"):
            owner.start(argv)
        finish = threading.Timer(.1, (tmp_path / "finish").touch)
        finish.start()
        try:
            assert owner.stop(terminate_timeout=2, kill_timeout=.5)["action"] == "stopped"
        finally:
            finish.join(timeout=1)
        assert child.wait(timeout=1) == 0 and read(directory)["state"] == "stopped"
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
