"""Service startup: the app factory with the in-process worker takes the lease and ticks; the CLI
doctor/create-user/backup commands work against a data dir; fixture URLs are control-plane-relative."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from fastapi.testclient import TestClient
from helpers import enrolled_agent, seed


def test_inprocess_worker_ticks_and_takes_lease(settings):
    from convoy_server.app import create_app

    settings.scheduler_interval_s = 0.2
    app = create_app(settings, start_scheduler=True)
    with TestClient(app) as c:
        assert (
            c.post(
                "/api/v1/auth/login", json={"email": "admin@example.com", "password": "admin-password-1"}
            ).status_code
            == 200
        )
        time.sleep(1.0)
        from convoy_server.db import session_scope
        from convoy_server.models import SchedulerLease

        with session_scope() as db:
            lease = db.get(SchedulerLease, "scheduler")
            assert lease is not None and lease.fence >= 1 and lease.owner
        assert c.get("/api/health").json()["db"]["journal_mode"] == "delete"


def test_cli_doctor_create_user_backup(settings, tmp_path):
    env = {**os.environ, "CONVOY_DATA_DIR": str(settings.data_dir), "CONVOY_SIMULATOR": "1"}

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "convoy_server.cli", *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    out = run("doctor")
    assert out.returncode == 0 and json.loads(out.stdout)["db"]["journal_mode"] == "delete", out.stderr
    out = run("create-user", "ops@example.com", "--role", "operator", "--password", "operator-pass-1")
    assert out.returncode == 0 and "created" in out.stdout, out.stderr
    out = run("backup", "--out", str(tmp_path / "b.db"))
    assert out.returncode == 0 and json.loads(out.stdout)["sha256"], out.stderr
    out = run("recover-admin", "x@example.com", "--password", "long-enough-password")
    assert out.returncode == 2 and "quarantine" in out.stdout  # only valid during restore quarantine


def test_fixture_manifest_urls_are_control_plane_relative(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    m = a.client.get(f"/api/agent/v1/releases/{s['release_id']}").json()
    assert (
        m["spec"]["model"]["file"]["url"].startswith("/api/sim/blobs/")
        and m["spec"]["model"]["file"]["auth"] == "device"
    )
    assert a.client.get(m["spec"]["model"]["file"]["url"]).status_code == 200
    assert TestClient(app).get(m["spec"]["model"]["file"]["url"]).status_code == 401  # device auth required


def test_worker_and_sim_fleet_entrypoints_stop_cleanly_on_sigterm(settings, tmp_path):
    """Container stop sends SIGTERM to PID 1: both long-running entrypoints must exit 0 within the
    bounded shutdown (observed before the fix: Docker had to SIGKILL them, exit 137)."""
    import signal
    import socket
    import threading

    import uvicorn
    from conftest import WEB, login
    from convoy_server.app import create_app

    env = {
        **os.environ,
        "CONVOY_DATA_DIR": str(settings.data_dir),
        "CONVOY_SIMULATOR": "1",
        "CONVOY_SCHEDULER_INTERVAL_S": "0.5",
    }
    # worker
    p = subprocess.Popen(
        [sys.executable, "-m", "convoy_server.cli", "worker"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    time.sleep(2.0)
    assert p.poll() is None
    p.send_signal(signal.SIGTERM)
    try:
        rc = p.wait(timeout=40)
    except subprocess.TimeoutExpired:
        p.kill()
        raise AssertionError("worker ignored SIGTERM") from None
    err = p.stderr.read()
    assert rc == 0, err[-2000:]
    # the worker process initialises logging at the configured level (INFO by default): its INFO lines
    # reach the container log. Before the fix only WARNING and above did, so a successful nightly
    # backup (logged at INFO with its id and timings) left no trace while deferrals (WARNING) did.
    assert "worker starting as" in err, err[-2000:]
    assert "received: stopping the worker" in err, err[-2000:]
    # sim-fleet against a real server
    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
    settings.public_url = f"http://127.0.0.1:{port}"
    app = create_app(settings, start_scheduler=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    admin = login(TestClient(app))
    seed(admin)
    toks = [
        admin.post("/api/v1/enrollments", json={"label": f"f{i}", "simulated": True}, headers=WEB).json()[
            "token"
        ]
        for i in range(2)
    ]
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    aenv = {**os.environ, "PYTHONPATH": os.path.join(root, "agent")}
    q = subprocess.Popen(
        [sys.executable, "-m", "convoy_agent.cli", "sim-fleet", "--server", settings.public_url, "--tokens", ",".join(toks), "--root", str(tmp_path / "fleet"), "--name-prefix", "sig"],
        env=aenv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        for _ in range(120):
            devs = admin.get("/api/v1/devices").json()
            if sum(1 for d in devs if d["status"] == "online") >= 2:
                break
            assert q.poll() is None, q.stderr.read()[-2000:]
            time.sleep(0.5)
        else:
            raise AssertionError("sim-fleet agents never came online")
        q.send_signal(signal.SIGTERM)
        try:
            rc = q.wait(timeout=45)
        except subprocess.TimeoutExpired:
            q.kill()
            raise AssertionError("sim-fleet ignored SIGTERM") from None
        assert rc == 0, q.stderr.read()[-3000:]
        for i in (1, 2):
            lock = tmp_path / "fleet" / f"sig-{i}" / "agent.lock"
            fd = os.open(str(lock), os.O_RDWR | os.O_CREAT)
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # released by the clean shutdown
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    finally:
        if q.poll() is None:
            q.kill()
        server.should_exit = True
        th.join(timeout=5)


def test_sim_fleet_shutdown_is_bounded_when_the_control_plane_is_unreachable(settings, tmp_path):
    """SIGTERM while the agents are blocked in control-plane retries: the process must still exit within
    its declared deadline (non-daemon threads never keep the interpreter alive past it)."""
    import signal
    import socket
    import threading

    import uvicorn
    from conftest import WEB, login
    from convoy_server.app import create_app

    with socket.socket() as s_:
        s_.bind(("127.0.0.1", 0))
        port = s_.getsockname()[1]
    settings.public_url = f"http://127.0.0.1:{port}"
    app = create_app(settings, start_scheduler=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)
    admin = login(TestClient(app))
    seed(admin)
    toks = [
        admin.post("/api/v1/enrollments", json={"label": f"u{i}", "simulated": True}, headers=WEB).json()[
            "token"
        ]
        for i in range(2)
    ]
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    aenv = {**os.environ, "PYTHONPATH": os.path.join(root, "agent")}
    q = subprocess.Popen(
        [sys.executable, "-m", "convoy_agent.cli", "sim-fleet", "--server", settings.public_url, "--tokens", ",".join(toks), "--root", str(tmp_path / "fleet"), "--name-prefix", "unr", "--stop-timeout", "5"],
        env=aenv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        for _ in range(120):
            if sum(1 for d in admin.get("/api/v1/devices").json() if d["status"] == "online") >= 2:
                break
            assert q.poll() is None, q.stderr.read()[-2000:]
            time.sleep(0.5)
        else:
            raise AssertionError("agents never came online")
        # the control plane disappears; the agents keep retrying reports/flushes
        server.should_exit = True
        th.join(timeout=10)
        time.sleep(2.0)
        t0 = time.monotonic()
        q.send_signal(signal.SIGTERM)
        try:
            rc = q.wait(timeout=25)
        except subprocess.TimeoutExpired:
            q.kill()
            raise AssertionError(
                "sim-fleet shutdown was not bounded with the control plane unreachable"
            ) from None
        assert rc in (0, 1) and time.monotonic() - t0 < 25, (rc, q.stderr.read()[-1500:])
    finally:
        if q.poll() is None:
            q.kill()
        server.should_exit = True
        th.join(timeout=5)
