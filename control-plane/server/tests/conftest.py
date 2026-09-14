from __future__ import annotations

import hashlib
import secrets
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from convoy_server import config, db  # noqa: E402
from convoy_server.app import create_app  # noqa: E402
from fastapi.testclient import TestClient

WEB = {"X-Convoy-Client": "web"}


@pytest.fixture()
def settings(tmp_path: Path):
    s = config.Settings(
        data_dir=tmp_path / "data",
        simulator=True,
        scheduler_inprocess=False,
        bootstrap_admin_email="admin@example.com",
        bootstrap_admin_password="admin-password-1",
        public_url="http://testserver",
        offline_after_s=90,
        grant_ttl_s=30,
    )
    db.reset_engine()
    config.set_settings(s)
    yield s
    db.reset_engine()


@pytest.fixture()
def app(settings):
    return create_app(settings, start_scheduler=False)


@pytest.fixture()
def client(app):
    with TestClient(app) as c:
        yield c


def login(client: TestClient, email="admin@example.com", password="admin-password-1") -> TestClient:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture()
def admin(client):
    return login(client)


def make_user(admin: TestClient, email: str, role: str, password: str = "password-123") -> dict:
    r = admin.post("/api/v1/users", json={"email": email, "role": role, "password": password}, headers=WEB)
    assert r.status_code == 201, r.text
    return r.json()


def api_token_client(app, admin: TestClient, name="t") -> TestClient:
    r = admin.post("/api/v1/tokens", json={"name": name}, headers=WEB)
    assert r.status_code == 201, r.text
    c = TestClient(app)
    c.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    return c


class FakeAgent:
    """Minimal device-side identity for API tests (real agent lives in convoy_agent)."""

    def __init__(self, app, name="sim-1"):
        self.client = TestClient(app)
        self.name = name
        self.secret = secrets.token_urlsafe(32)
        self.request_id = "req-" + secrets.token_hex(8)
        self.device_id: str | None = None
        self.seq = 0
        self.boot_id = "boot-" + secrets.token_hex(4)

    @property
    def secret_hash(self) -> str:
        return hashlib.sha256(self.secret.encode()).hexdigest()

    def enroll(self, token: str, simulated=True, **extra):
        r = self.client.post(
            "/api/agent/v1/enroll",
            json={
                "enrollment_token": token,
                "request_id": self.request_id,
                "secret_hash": self.secret_hash,
                "name": self.name,
                "simulated": simulated,
                "agent_version": "test",
                **extra,
            },
        )
        if r.status_code == 200:
            self.device_id = r.json()["device_id"]
            self.client.headers.update({"Authorization": f"Bearer cvd_{self.device_id}_{self.secret}"})
        return r


def enrollment_token(admin: TestClient, simulated=True, **kw) -> str:
    r = admin.post("/api/v1/enrollments", json={"label": "t", "simulated": simulated, **kw}, headers=WEB)
    assert r.status_code == 201, r.text
    return r.json()["token"]
