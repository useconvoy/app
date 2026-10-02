"""The public device chat contract (platform-chat-v1) end to end, on loopback:

* the real control plane (``convoy_server``, SQLite, in process under uvicorn);
* the real website: a production build of ``website/`` served by ``next start``, whose
  ``/api/platform/[...path]`` route handler is the proxy under test;
* a physical device enrolled and kept live through the real agent API (enrollment token,
  ``/api/agent/v1/enroll``, live heartbeats on ``/api/agent/v1/report``), which claims chat
  requests through ``/api/agent/v1/chat/claim`` and ``/result`` with the agent's own relay
  code (``convoy_agent.chat.run`` over ``convoy_agent.client.Client``). Only its model is
  scripted: it answers the planner's scene with the first pill the request lists as takeable;
* the planner's transport (``PlatformChatClient``) and endpoint, signed in through the website.

The releases are seeded rows (creating a physical release needs a model host); everything
else goes through HTTP. Needs the ``managed`` extra, Node and a website build
(``pnpm install && pnpm build`` in ``website/``); without them the test is skipped, unless
``CONVOY_REQUIRE_CHAT_CONTRACT=1`` (CI), which makes a missing piece a failure.

The tests run in file order on one stack. The website's send limits are per process and
per minute (6 per session, 20 per site), so each test that sends uses its own session and
the module stays under 20 sends.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

REQUIRED = os.environ.get("CONVOY_REQUIRE_CHAT_CONTRACT") == "1"
WEBSITE = Path(__file__).resolve().parents[3] / "website"
NEXT = WEBSITE / "node_modules" / "next" / "dist" / "bin" / "next"


def _missing(reason: str):
    if REQUIRED:
        pytest.fail(f"the chat contract test cannot run: {reason}", pytrace=False)
    pytest.skip(reason, allow_module_level=True)


try:
    import httpx
    import uvicorn
    from convoy_agent import chat as agent_chat
    from convoy_agent.client import Client as AgentClient
    from convoy_server import db
    from convoy_server.app import create_app
    from convoy_server.config import Settings
    from convoy_server.models import Release
except ImportError as error:  # pragma: no cover - depends on the installed extra
    _missing(f"install the managed extra ({error})")

from convoy_sim.bimanual_pill_task.configs import CONFIGS  # noqa: E402
from convoy_sim.bimanual_pill_task.device_planner import (  # noqa: E402
    TRANSPORT,
    AccessRefused,
    DevicePlannerEndpoint,
    DeviceSelectionError,
    PlatformChatClient,
    build_messages,
    model_label,
    sign_in,
)
from convoy_sim.bimanual_pill_task.evaluate import _device_problem  # noqa: E402

PROFILE = CONFIGS["edge_qwen_edge_skills"].skill_planner
RELEASE_A, RELEASE_B = "rel_contracta", "rel_contractb"
LEAKS = ("huggingface", "spec", "provenance", "model_path", "argv", "template", "binary_sha256", "created_by",
         "Traceback", "active release changed", "already has a chat request")


def _free_socket() -> socket.socket:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    return sock


def _seed_release(release_id: str, digest: str, repo: str, file: str, quantization: str) -> None:
    """A physical release row as the catalog records one (spec.model with the GGUF header's file type)."""
    with db.session_scope() as session, db.write_txn(session):
        session.add(Release(
            id=release_id, name=f"Contract {release_id[-1]}", version="1", digest=digest, build_status="ready",
            notes="private note", provenance={"argv_preview": ["--api-key-file", "<key>"]},
            spec={
                "schema_version": 1,
                "model": {"source": "hf", "repo": repo, "revision": "main", "commit": "c" * 40,
                          "file": {"path": file, "size": 1, "sha256": "e" * 64,
                                   "url": f"https://huggingface.co/{repo}/resolve/{'c' * 40}/{file}", "auth": None},
                          "gguf": {"file_type": quantization, "name": repo.split("/")[-1], "architecture": "qwen2"}},
                "template": {"text": "private template"},
                "runtime": {"name": "llama.cpp", "tag": "b6550", "commit": "5266f24da75dc449bd56cbed7addb9c8e4a6a73e",
                            "backend": "cuda"},
                "config": {"ctx_size": 2048, "n_predict": 128, "temperature": 0.0, "seed": 42},
            }))


def _first_available(text: str) -> str:
    """The scripted model: the first pill the request lists as takeable, else wait or done."""
    arm = re.search(r"Arm (L|R) is free", text)
    if arm is None:
        return "ok"
    pick = re.search(r"can pick now \(id: position\):\n(\d+): ", text)
    push = re.search(r"must push apart before picking \(id: position\):\n(\d+): ", text)
    if pick or push:
        return json.dumps({"arm": arm.group(1), "skill": "pick_and_drop" if pick else "push_apart",
                           "pill": int((pick or push).group(1))})
    return json.dumps({"arm": arm.group(1), "skill": "done" if "No pill is left" in text else "wait"})


class Device:
    """A physical device on the real agent API with a scripted model (see the module docstring)."""

    def __init__(self, control_plane: str, token: str, name: str):
        self.secret = secrets.token_urlsafe(32)
        enrolled = httpx.post(f"{control_plane}/api/agent/v1/enroll", timeout=10, json={
            "enrollment_token": token, "request_id": f"req-{secrets.token_hex(6)}",
            "secret_hash": hashlib.sha256(self.secret.encode()).hexdigest(), "name": name, "simulated": False,
            "agent_version": "contract-test"})
        assert enrolled.status_code == 200, enrolled.text
        self.device_id = enrolled.json()["device_id"]
        self.client = AgentClient(control_plane, f"cvd_{self.device_id}_{self.secret}", timeout=10)
        self.stop, self.paused, self.lock = threading.Event(), threading.Event(), threading.Lock()
        self.seq, self.nonce, self.release = 0, None, None
        self.served: list[dict] = []  # trace id and prompt of every request the model answered
        self.agent = SimpleNamespace(
            stop=self.stop, _stop_requested=False, simulate=False, journal={"active_release_id": None},
            sup=SimpleNamespace(release_id=None, config={"n_predict": 128}),
            gw=SimpleNamespace(mode="production", handle_relay=self.model), client=self.client)
        self.thread: threading.Thread | None = None

    def report(self, release: str) -> None:
        """A live heartbeat (echoing the last live nonce) that reports the active release and chat support."""
        with self.lock:
            self.seq += 1
            body = {"seq": self.seq, "kind": "heartbeat", "boot_id": "boot-contract", "agent_version": "contract-test",
                    "observed": {"generation": 0, "active_release_id": release, "stage": "active", "health": "ok",
                                 "gateway": {"mode": "production"}, "chat": {"supported": True, "protocol_version": 1},
                                 "runtime": {"backend": "cuda", "build_info": "b6550-5266f24d", "n_ctx": 2048,
                                             "gpu_offloaded_layers": 29, "gpu_total_layers": 29,
                                             "model_path": "/var/lib/private/model.gguf", "argv": ["--api-key-file", "/k"]}}}
            if self.nonce:
                body["live_nonce"] = self.nonce
            reply = self.client.post("/api/agent/v1/report", body, retries=0)
            assert reply["applied"], reply
            self.nonce, self.release = reply["live_nonce"], release
            self.agent.journal["active_release_id"] = self.agent.sup.release_id = release

    def model(self, body: dict, *, expected_release_id: str, deadline_s: float):
        text = body["messages"][-1]["content"]
        trace = f"tr_{secrets.token_hex(8)}"
        self.served.append({"trace_id": trace, "release_id": expected_release_id, "prompt": text})
        tokens_in = len(text) // 4
        return 200, {"choices": [{"message": {"content": _first_available(text)}, "finish_reason": "stop"}],
                     "usage": {"prompt_tokens": tokens_in, "completion_tokens": 12, "total_tokens": tokens_in + 12},
                     "convoy": {"release_id": expected_release_id, "simulated": False, "trace_id": trace,
                                "latency_ms": 41.5, "ttft_ms": 20.25, "queue_ms": 0.5}}

    def start(self, release: str) -> None:
        self.report(release)

        def loop():
            beat = time.monotonic()
            while not self.stop.is_set():
                if time.monotonic() - beat > 5:
                    self.report(self.release)
                    beat = time.monotonic()
                if not self.paused.is_set():
                    agent_chat.run(self.agent)  # one claim; the agent's real relay code
                self.stop.wait(0.05)

        self.thread = threading.Thread(target=loop, daemon=True, name=f"device-{self.device_id}")
        self.thread.start()


@dataclass
class Stack:
    web: str
    control_plane: str
    admin: httpx.Client
    device: Device
    passwords: dict[str, str]
    first_request: str | None = None  # a request the planner session made (read back by other sessions)
    first_session: str | None = None

    def session(self, email: str) -> str:
        """A new website session (a real sign-in through /api/platform/auth/login)."""
        return sign_in(self.web, email, self.passwords[email])

    def client(self, cookie: str, **kwargs) -> PlatformChatClient:
        return PlatformChatClient(self.web, cookie, **{"min_interval_s": 0.0, **kwargs})

    def raw(self, method: str, path: str, cookie: str | None, body=None, origin: str | None = None) -> httpx.Response:
        headers = {"Accept": "application/json", "X-Convoy-Client": "web"}
        if cookie:
            headers["Cookie"] = cookie
        if body is not None:
            headers["Origin"] = self.web if origin is None else origin
        return httpx.request(method, self.web + path, headers=headers, json=body, timeout=20)


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    if shutil.which("node") is None:
        _missing("node is not installed")
    if not NEXT.exists() or not (WEBSITE / ".next" / "BUILD_ID").exists():
        _missing("build the website first: pnpm install && pnpm build in website/")
    tmp = tmp_path_factory.mktemp("chat-contract")
    passwords = {email: secrets.token_urlsafe(18) for email in ("admin@example.test", "operator@example.test",
                                                                "viewer@example.test")}
    # Sign-in throttling is not under test here; each sending test signs in on its own.
    settings = Settings(data_dir=tmp / "server", simulator=True, scheduler_inprocess=False, offline_after_s=90,
                        login_throttle=100, bootstrap_admin_email="admin@example.test",
                        bootstrap_admin_password=passwords["admin@example.test"])
    db.reset_engine()
    api_socket = _free_socket()
    control_plane = f"http://127.0.0.1:{api_socket.getsockname()[1]}"
    settings.public_url = control_plane
    server = uvicorn.Server(uvicorn.Config(create_app(settings, start_scheduler=False), log_level="error"))
    api_thread = threading.Thread(target=server.run, kwargs={"sockets": [api_socket]}, daemon=True)
    api_thread.start()
    web_process = device = admin = None
    try:
        deadline = time.monotonic() + 15
        while not server.started and api_thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "the control plane did not start"
        admin = httpx.Client(base_url=control_plane, headers={"X-Convoy-Client": "web"}, timeout=10)
        assert admin.post("/api/v1/auth/login", json={"email": "admin@example.test",
                                                      "password": passwords["admin@example.test"]}).status_code == 200
        for role in ("operator", "viewer"):
            email = f"{role}@example.test"
            made = admin.post("/api/v1/users", json={"email": email, "role": role, "password": passwords[email]})
            assert made.status_code == 201, made.text
        _seed_release(RELEASE_A, "a" * 64, "example-org/Example-1B-GGUF", "example-1b-q4_k_m.gguf", "Q4_K_M")
        _seed_release(RELEASE_B, "b" * 64, "example-org/Example-3B-GGUF", "example-3b-q5_k_m.gguf", "Q5_K_M")

        def enrollment(simulated: bool) -> str:
            made = admin.post("/api/v1/enrollments", json={"label": "contract", "simulated": simulated})
            assert made.status_code == 201, made.text
            return made.json()["token"]

        # A simulated device too: the public list must leave it out.
        simulated = httpx.post(f"{control_plane}/api/agent/v1/enroll", timeout=10, json={
            "enrollment_token": enrollment(True), "request_id": "req-simulated", "secret_hash": "f" * 64,
            "name": "Simulated twin", "simulated": True, "agent_version": "contract-test"})
        assert simulated.status_code == 200, simulated.text
        device = Device(control_plane, enrollment(False), "Contract board")
        device.start(RELEASE_A)

        web_socket = _free_socket()
        web = f"http://127.0.0.1:{web_socket.getsockname()[1]}"
        web_socket.close()
        log = (tmp / "website.log").open("w")
        web_process = subprocess.Popen(
            ["node", str(NEXT), "start", "--hostname", "127.0.0.1", "--port", web.rsplit(":", 1)[1]], cwd=WEBSITE,
            env={**os.environ, "CONVOY_API_URL": control_plane, "CONVOY_CONSOLE_ORIGIN": web,
                 "NEXT_TELEMETRY_DISABLED": "1"}, stdout=log, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + 60
        while True:
            assert web_process.poll() is None, (tmp / "website.log").read_text()[-4000:]
            try:
                if httpx.get(f"{web}/api/platform/chat/devices", timeout=2).status_code == 401:
                    break
            except httpx.HTTPError:
                pass
            assert time.monotonic() < deadline, "the website did not start"
            time.sleep(0.2)
        yield Stack(web, control_plane, admin, device, passwords)
    finally:
        if web_process is not None:
            web_process.terminate()
            try:
                web_process.wait(10)
            except subprocess.TimeoutExpired:
                web_process.kill()
        if device is not None:
            device.stop.set()
            if device.thread is not None:
                device.thread.join(5)
        if admin is not None:
            admin.close()
        server.should_exit = True
        api_thread.join(10)
        db.reset_engine()


def _obs():
    """A scene where the left arm can pick pill 0 (the device_planner unit tests' scene)."""
    return {
        "pills": [
            {"id": "pill_00", "xy": [0.50, 0.12], "state": "on_mat", "last_status": None},
            {"id": "pill_01", "xy": [0.37, 0.0], "state": "in_bottle", "last_status": None},
            {"id": "pill_02", "xy": [0.48, -0.15], "state": "on_mat", "last_status": None},
        ],
        "arms": {
            "left": {"tcp": [0.33, 0.30, 0.87], "shoulder": [0.08, 0.17, 1.13], "links_xy": [[0.20, 0.25], [0.30, 0.30], [0.33, 0.30]],
                     "phase": None, "busy": False, "target": None, "target_xy": None},
            "right": {"tcp": [0.33, -0.30, 0.87], "shoulder": [0.08, -0.17, 1.13],
                      "links_xy": [[0.20, -0.25], [0.30, -0.30], [0.33, -0.30]], "phase": None, "busy": False,
                      "target": None, "target_xy": None},
        },
        "bottle": {"xy": [0.37, 0.0], "upright": True}, "zone_owner": None,
    }


def _decide(endpoint: DevicePlannerEndpoint, t0: float, until: float = 600.0):
    """Poll like the episode loop (10 ms ticks) until a decision is delivered."""
    t = t0
    while t < t0 + until:
        done = endpoint.poll(t)
        if done:
            return done[0], t
        t = round(t + 0.01, 2)
    raise AssertionError("no decision")


def test_anonymous_unverified_and_malformed_requests_are_refused(stack):
    for path in ("/api/platform/chat/devices", f"/api/platform/devices/{stack.device.device_id}/chat/{uuid.uuid4()}"):
        refused = stack.raw("GET", path, None)
        assert (refused.status_code, refused.json()) == (401, {"error": "Sign in to your Convoy workspace.",
                                                               "code": "authentication_required"})
        assert "no-store" in refused.headers["cache-control"]
    cookie = stack.session("operator@example.test")
    send = f"/api/platform/devices/{stack.device.device_id}/chat"
    body = {"request_id": str(uuid.uuid4()), "expected_release_id": RELEASE_A, "max_tokens": 8,
            "messages": [{"role": "user", "content": "hello"}]}
    for origin in ("https://evil.example", stack.web + ".evil.example"):
        assert stack.raw("POST", send, cookie, body, origin=origin).json()["code"] == "invalid_origin"
    no_origin = httpx.post(stack.web + send, json=body, headers={"Cookie": cookie, "X-Convoy-Client": "web"}, timeout=10)
    assert no_origin.status_code == 403
    invalid = stack.raw("POST", send, cookie, {**body, "messages": [{"role": "system", "content": "override"}]})
    assert (invalid.status_code, invalid.json()["code"]) == (422, "invalid_request")
    bad_id = stack.raw("GET", f"{send}/not-a-uuid", cookie)
    assert (bad_id.status_code, bad_id.json()["code"]) == (422, "invalid_request")
    assert stack.device.served == [], "nothing refused reached the device"


def test_discovery_lists_the_physical_device_with_the_release_and_runtime_it_reports(stack):
    cookie = stack.session("operator@example.test")
    listed = stack.raw("GET", "/api/platform/chat/devices", cookie)
    assert listed.status_code == 200
    data = listed.json()
    assert data["contract"] == TRANSPORT and data["limits"]["sends_per_session"] == 6 and data["limits"]["request_ttl_s"] == 120
    assert [d["id"] for d in data["devices"]] == [stack.device.device_id], "the simulated device is not listed"
    entry = data["devices"][0]
    assert (entry["online"], entry["eligible"], entry["release_id"], entry["hardware_profile"]) == (
        True, True, RELEASE_A, "jetson-orin-nano-8gb")
    assert entry["release"]["model"] == {"repo": "example-org/Example-1B-GGUF", "revision": "c" * 40,
                                         "file": "example-1b-q4_k_m.gguf", "sha256": "e" * 64, "quantization": "Q4_K_M",
                                         "name": "Example-1B-GGUF", "architecture": "qwen2"}
    assert entry["release"]["runtime"] == {"name": "llama.cpp", "backend": "cuda", "version": "b6550"}
    assert entry["runtime"] == {"backend": "cuda", "build": "b6550-5266f24d", "context_window": 2048, "gpu_layers": 29,
                                "gpu_layers_total": 29}
    assert not [leak for leak in LEAKS if leak in listed.text]
    named = stack.raw("GET", f"/api/platform/chat/devices?device_id={stack.device.device_id}", cookie).json()
    assert [d["id"] for d in named["devices"]] == [stack.device.device_id] and named["devices"][0]["release"]
    assert stack.raw("GET", "/api/platform/chat/devices?device_id=../x", cookie).json()["code"] == "invalid_request"
    client = stack.client(cookie)
    state = client.device()
    assert (state.device_id, state.release_id, client.release_id) == (stack.device.device_id, RELEASE_A, RELEASE_A)
    assert model_label(client.model) == "example-org/Example-1B-GGUF Q4_K_M" and client.min_interval_s == 0.0
    assert client.limits["sends_per_session"] == 6 and client.model["device_runtime_build"] == "b6550-5266f24d"


def test_the_planner_transport_sends_polls_and_records_trace_and_release_ids(stack):
    client = stack.client(stack.session("operator@example.test"), min_interval_s=1.0)
    client.device()
    outcome = client.send(build_messages(_obs(), "left"), 32)
    assert outcome.status == "succeeded" and outcome.release_id == RELEASE_A and outcome.polls >= 1
    assert json.loads(outcome.content) == {"arm": "L", "skill": "pick_and_drop", "pill": 0}
    served = stack.device.served[-1]
    assert outcome.trace_id == served["trace_id"] and re.fullmatch(r"tr_[0-9a-f]{16}", outcome.trace_id)
    assert (outcome.device_latency_ms, outcome.ttft_ms, outcome.queue_ms, outcome.tokens_out) == (41.5, 20.25, 0.5, 12)
    assert outcome.finish_reason == "stop" and outcome.e2e_ms > 0
    endpoint = DevicePlannerEndpoint(PROFILE, client, lambda side: _obs(), 3)
    endpoint.submit("left", {}, 0.0)
    decided, _ = _decide(endpoint, 0.0)
    assert decided.decision == {"kind": "skill", "skill_id": "pick_and_drop", "parameters": {"pill": "pill_00", "arm": "left"}}
    record = endpoint.records[0]
    assert (record["transport"], record["result"], record["release_id"], record["trace_id"]) == (
        TRANSPORT, "valid", RELEASE_A, stack.device.served[-1]["trace_id"])
    assert record["paced_wait_s"] > 0, "the second send waited for the client's spacing"
    stats = endpoint.stats(10.0)
    assert stats["transport"] == TRANSPORT and stats["model"]["model_repo"] == "example-org/Example-1B-GGUF"
    assert stats["decision_counts"] == {"resolved": 1, "first_call_accepted": 1, "reasked": 0, "failed": 0}
    stack.first_request = outcome.request_id
    stack.first_session = client._cookie


def test_requests_are_isolated_per_session_and_roles_are_the_backends(stack):
    path = f"/api/platform/devices/{stack.device.device_id}/chat/{stack.first_request}"
    mine = stack.raw("GET", path, stack.first_session)
    assert (mine.status_code, mine.json()["status"], mine.json()["release_id"]) == (200, "succeeded", RELEASE_A)
    assert mine.json()["id"] == stack.first_request and not [leak for leak in LEAKS if leak in mine.text]
    other = stack.raw("GET", path, stack.session("operator@example.test"))  # the same account, another sign-in
    assert (other.status_code, other.json()["code"]) == (404, "not_found")
    admin = stack.raw("GET", path, stack.session("admin@example.test"))
    assert admin.status_code == 404, "request ids are namespaced per session"
    viewer = stack.client(stack.session("viewer@example.test"))
    assert viewer.device().eligible  # viewers may list devices
    with pytest.raises(AccessRefused, match="403 forbidden"):
        viewer.send([{"role": "user", "content": "hello"}], 8)  # only operators send


def test_one_request_is_outstanding_and_the_deadline_holds(stack):
    cookie = stack.session("operator@example.test")
    client = stack.client(cookie, deadline_s=2.0, expire_wait_s=60.0)
    client.device()
    stack.device.paused.set()  # the device stops claiming
    try:
        timed_out = client.send([{"role": "user", "content": "first"}], 8)
        assert timed_out.status == "timeout" and 2000 <= timed_out.e2e_ms < 4000
        # The control plane allows one request in progress per device: another send is refused while it waits.
        busy = stack.raw("POST", f"/api/platform/devices/{stack.device.device_id}/chat", cookie, {
            "request_id": str(uuid.uuid4()), "expected_release_id": RELEASE_A, "max_tokens": 8,
            "messages": [{"role": "user", "content": "second"}]})
        assert (busy.status_code, busy.json()["code"]) == (409, "device_busy")
        assert not [leak for leak in LEAKS if leak in busy.text]
    finally:
        stack.device.paused.clear()
    after = client.send([{"role": "user", "content": "third"}], 8)
    assert after.status == "succeeded"
    assert client.late_results == [{**client.late_results[0], "request_id": timed_out.request_id, "status": "succeeded"}]
    prompts = [served["prompt"] for served in stack.device.served[-2:]]
    assert prompts == ["first", "third"], "the timed-out request finished before the next was sent"


def test_the_session_send_limit_answers_429_with_retry_after(stack):
    cookie = stack.session("operator@example.test")
    client = stack.client(cookie)
    client.device()
    for i in range(6):
        assert client.send([{"role": "user", "content": f"burst {i}"}], 8).status == "succeeded"
    limited = stack.raw("POST", f"/api/platform/devices/{stack.device.device_id}/chat", cookie, {
        "request_id": str(uuid.uuid4()), "expected_release_id": RELEASE_A, "max_tokens": 8,
        "messages": [{"role": "user", "content": "seventh"}]})
    assert (limited.status_code, limited.json()["code"]) == (429, "rate_limited")
    retry = int(limited.headers["retry-after"])
    assert 1 <= retry <= 60
    outcome = client.send([{"role": "user", "content": "eighth"}], 8)
    assert (outcome.status, outcome.http_status, outcome.error_code) == ("http_error", 429, "rate_limited")
    assert 1 <= outcome.retry_after_s <= 60
    assert client._not_before - client.clock() >= outcome.retry_after_s - 1, "the next send waits for Retry-After"
    assert [served["prompt"] for served in stack.device.served[-6:]] == [f"burst {i}" for i in range(6)]


def test_a_release_change_on_the_device_stops_the_planner(stack):
    client = stack.client(stack.session("operator@example.test"))
    client.device()
    endpoint = DevicePlannerEndpoint(PROFILE, client, lambda side: _obs(), 3)
    endpoint.submit("left", {}, 0.0)
    first, t = _decide(endpoint, 0.0)
    assert first.decision["kind"] == "skill"
    stack.device.report(RELEASE_B)  # the device now serves another release (a live report through the agent API)
    endpoint.submit("left", {}, t)
    stopped, _ = _decide(endpoint, t)
    assert stopped.decision == {"kind": "stop", "reason": "device_model_changed"}
    last = endpoint.records[-1]
    assert (last["result"], last["http_status"], last["error_code"], last["transport"]) == (
        "http_error", 409, "release_changed", TRANSPORT)
    assert endpoint.device_checks[-1]["release_id"] == RELEASE_B
    assert _device_problem(client).startswith("device model changed")
    assert client.release_id == RELEASE_A and client.model["quantization"] == "Q4_K_M", "the run's model is the one it started on"
    fresh = stack.client(stack.session("operator@example.test"))
    fresh.device()
    assert model_label(fresh.model) == "example-org/Example-3B-GGUF Q5_K_M", "a new run records the new model"


def test_several_physical_devices_must_be_named(stack):
    made = stack.admin.post("/api/v1/enrollments", json={"label": "second", "simulated": False})
    second = Device(stack.control_plane, made.json()["token"], "Second board")
    cookie = stack.session("operator@example.test")
    with pytest.raises(DeviceSelectionError, match="several devices.*--planner-device"):
        stack.client(cookie).device()
    named = stack.client(cookie, device_id=stack.device.device_id)
    assert named.device().device_id == stack.device.device_id
    other = stack.client(cookie, device_id=second.device_id).device()
    assert (other.online, other.eligible, other.release_id) == (False, False, None)
