"""Provision and exercise the real console BFF, API, worker and simulated device.

Runs inside the reference image. No fake routes, private database writes or
embedded operator credentials; services.py supplies this installation's login.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

import httpx
from convoy_agent.agent import AgentConfig, enroll

INSTALLATION = Path("/robot/installation.json")
RELEASE = Path("/app/release.json")


def save(value: dict):
    temporary = INSTALLATION.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.chmod(0o600)
    temporary.replace(INSTALLATION)


def wait_for(read, accept, seconds=130):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if accept(value):
            return value
        time.sleep(0.1)
    raise RuntimeError("service acceptance timed out; inspect compose logs")


class Console:
    def __init__(self):
        self.client = httpx.Client(base_url="http://web:3000", timeout=15, headers={
            "Origin": os.environ["CONVOY_CONSOLE_ORIGIN"], "X-Convoy-Client": "web",
        })
        self.post("auth/login", {"email": os.environ["CONVOY_ADMIN_EMAIL"],
                                 "password": os.environ["CONVOY_ADMIN_PASSWORD"]})

    def request(self, method, path, body=None, key=None):
        response = self.client.request(method, "/api/platform/" + path, json=body,
                                       headers={"Idempotency-Key": key or str(uuid.uuid4())})
        if not response.is_success:
            raise RuntimeError(f"console {method} {path} returned {response.status_code}")
        return response.json()

    def post(self, path, body, key=None):
        return self.request("POST", path, body, key)

    def get(self, path):
        return self.request("GET", path)

    def close(self):
        try:
            self.post("auth/logout", {})
        finally:
            self.client.close()


def bootstrap(api: Console):
    existing = json.loads(INSTALLATION.read_text()) if INSTALLATION.exists() else {}
    if "robot_id" in existing:
        api.get(f"deployments/{existing['deployment_id']}")
        if existing["manifest"] != json.loads(RELEASE.read_text()):
            raise RuntimeError("installed reference release changed; use a fresh local installation")
        return existing
    installation_id = existing.get("installation_id", str(uuid.uuid4()))
    save({"installation_id": installation_id})

    def create(step, path, body):
        return api.post(path, body, f"local:{installation_id}:{step}")

    manifest = json.loads(RELEASE.read_text())
    project = create("project", "projects", {"name": "Local service qualification"})
    device = AgentConfig(Path("/robot/device"))
    if not device.data.get("device_id"):
        token = api.post("enrollments", {"label": "Local simulator", "simulated": True})
        enrolled = enroll(Path("/robot/device"), server="https://api:8443", token=token["token"],
                          name="Virtual Sawyer", simulate=True, ca_file="/run/ca/ca.crt")
        device_id = enrolled["device_id"]
    else:
        device_id = device.data["device_id"]
    robot = create("robot", "robots", {"project_id": project["id"], "device_id": device_id,
                                      "name": "Virtual Sawyer", "profile": manifest["profile"]})
    app = create("application", "applications", {"project_id": project["id"], "name": "Scripted pick and place"})
    release = create("release", f"applications/{app['id']}/releases", {"manifest": manifest})
    deployment = create("deployment", "deployments", {"robot_id": robot["id"], "release_id": release["id"],
                                                       "expected_generation": 0})
    result = {"installation_id": installation_id, "project_id": project["id"], "robot_id": robot["id"],
              "application_id": app["id"], "release_id": release["id"], "release_digest": release["digest"],
              "deployment_id": deployment["id"], "generation": deployment["generation"], "manifest": manifest}
    save(result)
    return result


def verify(api: Console):
    installation = json.loads(INSTALLATION.read_text())
    deployment = wait_for(lambda: api.get(f"deployments/{installation['deployment_id']}"),
                          lambda row: row["state"] in {"ready", "blocked"})
    assert deployment["state"] == "ready", "device did not acknowledge the pinned worker release"
    # Browser-origin enforcement is exercised on the real Next.js server.
    denied = api.client.post("/api/platform/projects", json={"name": "must not be created"},
                             headers={"Origin": "https://unrelated.invalid"})
    assert denied.status_code == 403
    with httpx.Client(base_url="https://api:8443", timeout=5) as management:
        health = management.get("/api/health")
        health.raise_for_status()
        database = health.json()["db"]
    assert database["backend"] == "postgresql", database
    body = {"deployment_id": deployment["id"], "expected_generation": deployment["generation"],
            "seed": 0, "ttl_s": 120}
    key = str(uuid.uuid4())
    path = f"robots/{installation['robot_id']}/missions"
    mission = api.post(path, body, key)
    duplicate = api.post(path, body, key)
    assert duplicate["id"] == mission["id"], "idempotent start created another mission"
    final = wait_for(lambda: api.get(f"missions/{mission['id']}"),
                     lambda row: row["state"] in {"completed", "failed", "cancelled", "unknown"})
    assert final["state"] == "completed", final
    episode = api.get(f"episodes/{final['episode_id']}")
    assert episode["summary"]["final_success"] is True, episode
    assert episode["summary"]["steps"] == 500, episode
    journal_path = "/robot/device/coordinator/execution.sqlite3"
    with sqlite3.connect(f"file:{journal_path}?mode=ro", uri=True) as journal:
        applied = journal.execute("SELECT count(*) FROM commands WHERE mission_id=? AND state='applied'",
                                  (mission["id"],)).fetchone()[0]
    assert applied == 500, "episode summary does not match durable device action evidence"
    return {"policy_kind": "scripted", "physics": "MuJoCo", "execution": "offline lockstep",
            "profile": installation["manifest"]["profile"], "release_digest": installation["release_digest"],
            "database": database, "mission": final, "episode": episode, "journal_applied_actions": applied,
            "checks": ["console session login", "cross-origin mutation rejected", "TLS certificate verification",
                       "PostgreSQL metadata", "device readiness acknowledgement", "idempotent mission start",
                       "separate inference and simulator processes", "500 journaled physics actions", "task success"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["bootstrap", "verify"])
    args = parser.parse_args()
    api = Console()
    try:
        result = bootstrap(api) if args.action == "bootstrap" else verify(api)
    finally:
        api.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
