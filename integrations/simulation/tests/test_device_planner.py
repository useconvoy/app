"""The on-device planner of the Edge Qwen configuration: prompt, strict parsing, choice check, failure policy,
transport and episode wiring. The network is mocked here (and only here): no test reaches a device."""

import json
import re
import uuid

import numpy as np
import pytest

from convoy_sim.bimanual_pill_task.configs import CONFIGS, SLICES, EpisodeSpec, release_manifest
from convoy_sim.bimanual_pill_task.device_planner import (
    EXAMPLE_OBSERVATION,
    EXAMPLE_REPLY,
    MAX_TOKENS,
    PROMPT_VERSION,
    Action,
    ChatOutcome,
    DevicePlannerEndpoint,
    DeviceState,
    FailurePolicy,
    PortalChatClient,
    ReplyError,
    SessionEnded,
    build_messages,
    build_prompt,
    check_choice,
    parse_reply,
    scene,
)
from convoy_sim.bimanual_pill_task.episode import Episode, run_episode
from convoy_sim.bimanual_pill_task.offline_replay import OfflineReplayRecorder, episode_metrics

COOKIE = "convoy_session=cvs_" + "x" * 24


def _obs(**changes):
    """A small scene: the left arm free at rest, the right arm busy with pill 3."""
    obs = {
        "pills": [
            {"id": "pill_00", "xy": [0.50, 0.12], "state": "on_mat", "last_status": None},
            {"id": "pill_01", "xy": [0.37, 0.0], "state": "in_bottle", "last_status": None},
            {"id": "pill_02", "xy": [0.48, -0.15], "state": "on_mat", "last_status": None},
            {"id": "pill_03", "xy": [0.46, -0.05], "state": "held", "last_status": None},
            {"id": "pill_04", "xy": [0.55, 0.20], "state": "on_mat", "last_status": "no_clear_grasp"},
            {"id": "pill_05", "xy": [0.47, 0.02], "state": "on_mat", "last_status": None},
        ],
        "arms": {
            "left": {"tcp": [0.33, 0.30, 0.87], "shoulder": [0.08, 0.17, 1.13],
                     "links_xy": [[0.20, 0.25], [0.30, 0.30], [0.33, 0.30]], "phase": None, "busy": False,
                     "target": None, "target_xy": None},
            "right": {"tcp": [0.46, -0.05, 0.80], "shoulder": [0.08, -0.17, 1.13],
                      "links_xy": [[0.30, -0.20], [0.42, -0.08], [0.46, -0.05]], "phase": "descend", "busy": True,
                      "target": "pill_03", "target_xy": [0.46, -0.05]},
        },
        "bottle": {"xy": [0.37, 0.0], "upright": True}, "zone_owner": None,
    }
    obs.update(changes)
    return obs


# ---- prompt -----------------------------------------------------------------------------------------------


def test_prompt_describes_the_scene_as_text_for_the_free_arm():
    obs = _obs()
    text = build_prompt(obs, "left")
    assert text.startswith("You plan for a two-arm robot") and "Arm L is free. Choose the next action for arm L." in text
    assert "Bottle at (37, 0)." in text and "Arm L: free, gripper at (33, 30)." in text
    assert "Arm R: busy with pill 3 (descend)." in text
    assert "In the bottle: 1 (1 of 6 pills)." in text
    rest = text.split("Pills on the table that arm L can pick now (id: position):\n")[1]
    pick, rest = rest.split("Pills on the table that arm L must push apart before picking (id: position):\n")
    push, cannot = rest.split("Pills on the table that arm L cannot take now (id: position, reason):\n")
    assert pick.splitlines() == ["0: (50, 12)"]
    assert push.splitlines() == ["4: (55, 20), last pick no_clear_grasp"]
    # 4.7 cm from the right arm's target and 5 cm from its fingertips: the executive's 12 cm rule
    assert cannot.splitlines()[:2] == ["2: (48, -15), out of reach", "5: (47, 2), next to arm R"]
    assert "3:" not in pick + push + cannot.split("Actions:")[0]  # held by the other arm: not on the table
    assert '{"arm": "L", "skill": "pick_and_drop", "pill": ID}' in text and text.endswith("and nothing else.")


def test_messages_are_one_worked_example_then_the_request_within_the_chat_limits():
    obs = _obs()
    messages = build_messages(obs, "left", feedback="Your previous reply \"x\" was refused: because. Reply again.")
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[1]["content"] == EXAMPLE_REPLY
    assert check_choice(parse_reply(EXAMPLE_REPLY), EXAMPLE_OBSERVATION, "right") is None  # the example is right
    assert messages[2]["content"].endswith("Your previous reply \"x\" was refused: because. Reply again.")
    assert PROMPT_VERSION == "pill-planner-v5" and MAX_TOKENS <= 128
    # A 30-pill table stays inside the device chat API's limits: 16 messages, 8 KiB of text.
    episode = Episode(EpisodeSpec(CONFIGS["edge_qwen_edge_skills"], SLICES["pill_count_30"], 0, 5.0),
                      planner=_ScriptedTransport([]))
    big = build_messages(episode.observation("right"), "right")
    assert len(big) <= 16 and sum(len(m["content"].encode()) for m in big) <= 8192


def test_reach_and_separation_follow_the_executive_rules():
    obs = _obs()
    s = scene(obs, "left")
    assert s.available == [0, 4]
    assert s.pills[2].reach == "R" and s.pills[5].reach == "L+R"
    assert scene(obs, "right").pills[0].note == "out of reach"
    # The other arm holds the bottle zone: a pill the left arm reaches past the bottle is blocked.
    zone = _obs(zone_owner="right")
    zone["pills"][0]["xy"] = [0.42, 0.05]
    zone["arms"]["right"].update(target=None, target_xy=None, links_xy=[[0.2, -0.25], [0.3, -0.3], [0.33, -0.3]])
    assert scene(zone, "left").pills[0].note == "bottle zone in use by arm R"


# ---- strict parsing --------------------------------------------------------------------------------------


@pytest.mark.parametrize("text, action", [
    ('{"arm": "L", "skill": "pick_and_drop", "pill": 4}', Action("L", "pick_and_drop", 4)),
    ('  {"arm":"R","skill":"push_apart","pill":12}\n', Action("R", "push_apart", 12)),
    ('{"arm": "L", "skill": "wait"}', Action("L", "wait")),
    ('{"skill": "done", "arm": "R"}', Action("R", "done")),
])
def test_parser_accepts_exactly_the_declared_shapes(text, action):
    assert parse_reply(text) == action


@pytest.mark.parametrize("text, kind, reason", [
    (None, "invalid_json", "empty"),
    ("   ", "invalid_json", "empty"),
    ('```json\n{"arm": "L", "skill": "wait"}\n```', "invalid_json", "code block"),
    ('{"arm": "L", "skill": "wait"} I chose to wait.', "invalid_json", "nothing else"),
    ('Sure! {"arm": "L", "skill": "wait"}', "invalid_json", "nothing else"),
    ('{"arm": "L", "skill": "pick_and_drop", "pill": 4', "invalid_json", "nothing else"),  # cut at max_tokens
    ('{"arm": "L", "arm": "R", "skill": "wait"}', "invalid_json", "nothing else"),  # duplicate key
    ('{"arm": "L", "skill": "pick_and_drop", "pill": NaN}', "invalid_json", "nothing else"),
    ('"done"', "invalid_schema", "object"),
    ('[{"arm": "L", "skill": "wait"}]', "invalid_schema", "object"),
    ('{"arm": "L", "skill": "pick"}', "invalid_schema", "skill must be"),
    ('{"arm": "L", "skill": "wait", "pill": 3}', "invalid_schema", "exactly the keys"),
    ('{"arm": "L", "skill": "pick_and_drop"}', "invalid_schema", "exactly the keys"),
    ('{"arm": "L", "skill": "pick_and_drop", "pill": 3, "why": "near"}', "invalid_schema", "exactly the keys"),
    ('{"arm": "left", "skill": "wait"}', "invalid_schema", "arm must be"),
    ('{"arm": "L", "skill": "pick_and_drop", "pill": "4"}', "invalid_schema", "integer"),
    ('{"arm": "L", "skill": "pick_and_drop", "pill": 4.0}', "invalid_schema", "integer"),
    ('{"arm": "L", "skill": "pick_and_drop", "pill": true}', "invalid_schema", "integer"),
    ('{"arm": "L", "skill": "pick_and_drop", "pill": -1}', "invalid_schema", "integer"),
])
def test_parser_refuses_everything_else_without_repairing_it(text, kind, reason):
    with pytest.raises(ReplyError) as refused:
        parse_reply(text)
    assert refused.value.kind == kind and reason in refused.value.reason


# ---- choice check ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("action, code", [
    (Action("R", "pick_and_drop", 0), "wrong_arm"),
    (Action("L", "pick_and_drop", 9), "unknown_pill"),
    (Action("L", "pick_and_drop", 1), "pill_in_bottle"),
    (Action("L", "push_apart", 3), "taken_by_other_arm"),
    (Action("L", "pick_and_drop", 2), "out_of_reach"),
    (Action("L", "pick_and_drop", 5), "pill_blocked"),
    (Action("L", "wait"), "wait_with_pill_available"),
    (Action("L", "done"), "done_with_pills_on_table"),
    (Action("L", "pick_and_drop", 4), "needs_push_apart"),
    (Action("L", "push_apart", 0), "push_not_needed"),
    (Action("L", "pick_and_drop", 0), None),
    (Action("L", "push_apart", 4), None),
])
def test_choices_the_scene_rules_out_are_refused(action, code):
    refusal = check_choice(action, _obs(), "left")
    assert (refusal[0] if refusal else None) == code


def test_pills_are_given_up_after_the_stand_in_planners_limits():
    obs = _obs()
    obs["pills"][0].update(attempts=4, last_status="grasp_failed")
    obs["pills"][4].update(pushes=2)  # last pick no_clear_grasp, pushed twice already
    s = scene(obs, "left")
    assert s.pills[0].note == "given up after 4 picks" and s.pills[4].note.startswith("given up: pushed 2 times")
    assert s.available == [] and check_choice(Action("L", "pick_and_drop", 0), obs, "left")[0] == "given_up"
    assert check_choice(Action("L", "wait"), obs, "left") is None
    obs["pills"][0].update(attempts=1, last_status="unreachable")
    assert scene(obs, "left").pills[0].note == "given up: last try unreachable"


def test_wait_and_done_are_valid_only_when_the_scene_allows_them():
    obs = _obs()
    for pill in obs["pills"]:
        if pill["id"] in ("pill_00", "pill_04"):
            pill["state"] = "in_bottle"
    assert check_choice(Action("L", "wait"), obs, "left") is None  # pills 2 and 5 remain, neither available to L
    assert check_choice(Action("L", "done"), obs, "left")[0] == "done_with_pills_on_table"
    for pill in obs["pills"]:
        if pill["state"] == "on_mat":
            pill["state"] = "in_bottle"
    assert check_choice(Action("L", "done"), obs, "left") is None  # pill 3 is still held by R: done for the table


# ---- failure policy (mocked transport) ------------------------------------------------------------------


class _ScriptedTransport:
    """Answers from a script: each item is a reply text, or a ChatOutcome field dict for a failure."""

    def __init__(self, script, *, online=True, e2e_ms=2000.0):
        self.script, self.online, self.e2e_ms = list(script), online, e2e_ms
        self.release_id = "rel_test"
        self.sent: list[list[dict]] = []
        self.checks = 0

    def device(self):
        self.checks += 1
        return DeviceState(self.online, self.online, None if self.online else "offline", "rel_test",
                           "online" if self.online else "offline", 128, 2048, "2026-01-01T00:00:00Z", 200)

    def send(self, messages, max_tokens):
        assert max_tokens == MAX_TOKENS
        self.sent.append(messages)
        item = self.script.pop(0) if self.script else '{"arm": "L", "skill": "wait"}'
        base = {"request_id": str(uuid.uuid4()), "e2e_ms": self.e2e_ms, "sent_at": "t0", "finished_at": "t1"}
        if isinstance(item, dict):
            return ChatOutcome(**{**base, **item})
        return ChatOutcome(**base, status="succeeded", content=item, trace_id=f"tr_{len(self.sent):04x}",
                           device_latency_ms=900.0, ttft_ms=400.0, tokens_in=1000, tokens_out=21)


def _endpoint(script, *, pills=6, obs=None, **kwargs):
    transport = _ScriptedTransport(script, **kwargs)
    seen = []
    endpoint = DevicePlannerEndpoint(CONFIGS["edge_qwen_edge_skills"].skill_planner, transport,
                                     lambda side: obs or _obs(), pills, on_record=seen.append)
    return endpoint, transport, seen


def _run(endpoint, t0=0.0, until=60.0, dt=0.01):
    """Poll like the episode loop (10 ms ticks) until a decision is delivered."""
    t = t0
    while t < until:
        done = endpoint.poll(t)
        if done:
            return done[0], t
        t = round(t + dt, 2)
    raise AssertionError("no decision")


def test_a_valid_reply_is_delivered_after_its_measured_round_trip():
    endpoint, transport, seen = _endpoint(['{"arm": "L", "skill": "pick_and_drop", "pill": 0}'])
    call = endpoint.submit("left", {}, 0.0)
    delivered, t = _run(endpoint)
    assert delivered is call and call.decision == {"kind": "skill", "skill_id": "pick_and_drop",
                                                   "parameters": {"pill": "pill_00", "arm": "left"}}
    assert t == pytest.approx(2.0, abs=0.011)  # the arm held for exactly the 2.0 s round trip (10 ms ticks)
    record = endpoint.records[0]
    assert record["result"] == "valid" and record["next"] == "delivered" and record["trace_id"] == "tr_0001"
    assert record["sim_start_s"] == 0.0 and record["sim_end_s"] == 2.0 and record["prompt_version"] == PROMPT_VERSION
    assert record["messages"] == transport.sent[0] and record["content"].startswith('{"arm"')
    assert seen and seen[0]["status"] == "ok" and seen[0]["latency_ms"] == 2000.0


def test_refused_replies_are_re_asked_with_the_reason_then_counted_as_a_failed_decision():
    endpoint, transport, _ = _endpoint(['```json\n{"arm": "L", "skill": "wait"}\n```',
                                        '{"arm": "L", "skill": "pick_and_drop", "pill": 1}',
                                        '{"arm": "L", "skill": "pick_and_drop", "pill": 2}'])
    endpoint.submit("left", {}, 0.0)
    delivered, t = _run(endpoint)
    assert delivered.decision["kind"] == "failed"
    assert [r["result"] for r in endpoint.records] == ["invalid_json", "invalid_choice", "invalid_choice"]
    assert [r["refusal_code"] for r in endpoint.records] == ["invalid_json", "pill_in_bottle", "out_of_reach"]
    assert [r["next"] for r in endpoint.records] == ["re-asked", "re-asked", "failed_decision"]
    assert t == pytest.approx(6.0, abs=0.04)  # three measured round trips, each started on the next tick
    second, third = transport.sent[1][-1]["content"], transport.sent[2][-1]["content"]
    assert "was refused: the JSON object must not be wrapped in a ``` code block" in second
    assert "was refused: pill 1 is already in the bottle" in third
    assert endpoint.stats(t)["failed_decisions"] == 1


def test_transport_failures_are_recorded_as_such_and_re_asked_without_feedback():
    endpoint, transport, _ = _endpoint([{"status": "http_error", "http_status": 503, "e2e_ms": 300.0},
                                        {"status": "timeout", "http_status": 202, "e2e_ms": 45000.0},
                                        '{"arm": "L", "skill": "push_apart", "pill": 4}'])
    endpoint.submit("left", {}, 0.0)
    delivered, t = _run(endpoint, until=80.0)
    assert delivered.decision["skill_id"] == "push_apart"
    assert [r["result"] for r in endpoint.records] == ["http_error", "timeout", "valid"]
    assert t == pytest.approx(0.3 + 45.0 + 2.0, abs=0.04)  # a timeout costs its whole wait in sim time
    assert transport.checks == 2  # the device was checked after each transport failure, and was online
    assert all("was refused" not in m[-1]["content"] for m in transport.sent)


def test_the_device_going_offline_stops_the_episode():
    endpoint, transport, _ = _endpoint([{"status": "http_error", "http_status": 409, "e2e_ms": 250.0}], online=False)
    endpoint.submit("left", {}, 0.0)
    delivered, _ = _run(endpoint)
    assert delivered.decision == {"kind": "stop", "reason": "device_offline"}
    assert endpoint.stopped == "device_offline" and endpoint.device_checks[0]["online"] is False
    later = endpoint.submit("right", {}, 1.0)
    assert _run(endpoint, t0=1.0)[0] is later and later.decision["reason"] == "device_offline"
    assert len(transport.sent) == 1  # nothing more was sent


def test_the_call_budget_ends_the_episode_and_a_refused_session_stops_it():
    policy = FailurePolicy()
    assert policy.budget(24) == 60 and policy.budget(30) == 72
    endpoint, transport, _ = _endpoint(['{"arm": "L", "skill": "wait"}'] * 3)
    endpoint.budget = 2
    endpoint.submit("left", {}, 0.0)
    first, t = _run(endpoint)  # "wait" with pills available to L: refused, re-asked once, then the budget ends
    assert first.decision == {"kind": "stop", "reason": "planner_call_budget_exhausted"} and len(transport.sent) == 2

    class Ended(_ScriptedTransport):
        def send(self, messages, max_tokens):
            raise SessionEnded("401")

    ended = DevicePlannerEndpoint(CONFIGS["edge_qwen_edge_skills"].skill_planner, Ended([]), lambda side: _obs(), 6)
    ended.submit("left", {}, 0.0)
    assert _run(ended)[0].decision == {"kind": "stop", "reason": "session_ended"} and not ended.records


# ---- transport (mocked HTTP) -----------------------------------------------------------------------------


class _Response:
    def __init__(self, status, body, headers=None):
        self.status, self.headers, self._body = status, headers or {}, json.dumps(body).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakePortal:
    """The website's chat routes: POST answers 202 queued, reads report running until `ready_after` reads."""

    def __init__(self, *, ready_after=2, post_status=202, never_finish=False):
        self.ready_after, self.post_status, self.never_finish = ready_after, post_status, never_finish
        self.requests, self.reads = [], {}

    def open(self, request, timeout=None):
        self.requests.append(request)
        path = request.full_url.split("example.test", 1)[1]
        if path == "/api/portal/snapshot":
            return _Response(200, {"device": {"status": "online"}, "chat": {
                "online": True, "eligible": True, "release_id": "rel_live", "max_tokens": 128, "context_window": 2048}})
        if request.get_method() == "POST":
            body = json.loads(request.data)
            if self.post_status != 202:
                return _Response(self.post_status, {"error": {"code": "rate_limited"}}, {"Retry-After": "60"})
            self.reads[body["request_id"]] = 0
            return _Response(202, {"id": body["request_id"], "status": "queued"})
        request_id = path.rsplit("/", 1)[1]
        self.reads[request_id] += 1
        if self.never_finish or self.reads[request_id] < self.ready_after:
            return _Response(200, {"id": request_id, "status": "running"})
        return _Response(200, {"id": request_id, "status": "succeeded", "content": '{"arm": "L", "skill": "wait"}',
                               "finish_reason": "stop", "trace_id": "tr_00ab", "release_id": "rel_live",
                               "usage": {"prompt_tokens": 1034, "completion_tokens": 13, "total_tokens": 1047},
                               "metrics": {"latency_ms": 812.4, "ttft_ms": 455.0, "queue_ms": 0.2}})


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _client(portal, **kwargs):
    clock = _Clock()
    client = PortalChatClient("https://example.test", COOKIE, opener=portal, clock=clock, sleep=clock.sleep,
                              wall=lambda: 1.9e9 + clock.now, **kwargs)
    return client, clock


def test_portal_client_sends_the_chat_api_body_and_measures_the_round_trip():
    portal = _FakePortal(ready_after=3)
    client, clock = _client(portal)
    assert client.device().release_id == "rel_live"
    outcome = client.send([{"role": "user", "content": "scene"}], 32)
    post = portal.requests[1]
    body = json.loads(post.data)
    assert set(body) == {"request_id", "expected_release_id", "messages", "max_tokens"}  # no temperature: not settable
    assert uuid.UUID(body["request_id"]).version == 4 and body["expected_release_id"] == "rel_live"
    headers = dict(post.header_items())
    assert headers["Origin"] == "https://example.test" and headers["X-convoy-client"] == "web"
    assert headers["Cookie"] == COOKIE and COOKIE not in repr(client) and "cvs_" not in str(outcome)
    assert outcome.status == "succeeded" and outcome.trace_id == "tr_00ab" and outcome.polls == 3
    assert outcome.e2e_ms == pytest.approx(750.0)  # three 0.25 s reads after the POST
    assert (outcome.device_latency_ms, outcome.ttft_ms, outcome.tokens_in, outcome.tokens_out) == (812.4, 455.0, 1034, 13)


def test_portal_client_paces_sends_to_the_route_rate_limit():
    portal = _FakePortal(ready_after=1)
    client, clock = _client(portal)
    client.device()
    times = []
    for _ in range(4):
        client.send([{"role": "user", "content": "scene"}], 32)
        times.append(next(r for r in reversed(portal.requests) if r.get_method() == "POST") and clock.now)
    posts = [r for r in portal.requests if r.get_method() == "POST"]
    assert len(posts) == 4
    # 6 sends per minute per session: consecutive POSTs are at least 10.5 s apart.
    gaps = np.diff([t - 0.25 for t in times])
    assert np.all(gaps >= 10.5 - 1e-9)


def test_portal_client_reports_http_errors_honours_retry_after_and_never_overlaps_a_timed_out_request():
    limited = _FakePortal(post_status=429)
    client, clock = _client(limited)
    client.device()
    outcome = client.send([{"role": "user", "content": "scene"}], 32)
    assert (outcome.status, outcome.http_status, outcome.error_code) == ("http_error", 429, "rate_limited")
    before = clock.now
    client.send([{"role": "user", "content": "scene"}], 32)
    assert clock.now - before >= 60  # waited for Retry-After before the next POST

    slow = _FakePortal(never_finish=True)
    client, clock = _client(slow, deadline_s=45.0, expire_wait_s=20.0)
    client.device()
    timed_out = client.send([{"role": "user", "content": "scene"}], 32)
    assert timed_out.status == "timeout" and timed_out.e2e_ms >= 45000
    slow.never_finish = False
    client.send([{"role": "user", "content": "scene"}], 32)
    assert client.late_results[0]["request_id"] == timed_out.request_id
    assert client.late_results[0]["status"] == "succeeded"  # read to its end before anything new was sent


def test_portal_client_raises_when_the_session_is_refused():
    class Refused(_FakePortal):
        def open(self, request, timeout=None):
            import urllib.error

            raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    client, _ = _client(Refused())
    with pytest.raises(SessionEnded):
        client.device()
    with pytest.raises(ValueError):
        PortalChatClient("http://example.test", COOKIE)  # never a session over plain http


# ---- episode wiring --------------------------------------------------------------------------------------


class _FirstAvailable(_ScriptedTransport):
    """Test double for the device: picks the first pill the request lists as takeable (or waits / is done)."""

    def send(self, messages, max_tokens):
        self.sent.append(messages)
        text = messages[-1]["content"]
        arm = re.search(r"Arm (L|R) is free", text).group(1)
        pick = re.search(r"can pick now \(id: position\):\n(\d+): ", text)
        push = re.search(r"must push apart before picking \(id: position\):\n(\d+): ", text)
        if pick or push:
            reply = {"arm": arm, "skill": "pick_and_drop" if pick else "push_apart", "pill": int((pick or push).group(1))}
        else:
            reply = {"arm": arm, "skill": "done" if "No pill is left" in text else "wait"}
        return ChatOutcome(request_id=str(uuid.uuid4()), status="succeeded", e2e_ms=1500.0, sent_at="t0",
                           finished_at="t1", content=json.dumps(reply), trace_id=f"tr_{len(self.sent):04x}",
                           device_latency_ms=800.0, ttft_ms=400.0, tokens_in=900, tokens_out=21)


def test_a_decision_that_meets_the_busy_bottle_zone_is_held_then_started_or_rejected():
    episode = Episode(EpisodeSpec(CONFIGS["edge_qwen_edge_skills"], SLICES["nominal"], 0, 30.0),
                      planner=_ScriptedTransport([]))
    episode.space.owner = "right"  # the right arm is transferring a pill over the bottle
    pill = next(p.index for p in episode.world.pills() if episode._arm_conflict("left", p.index) == "bottle_zone_in_use")
    decision = {"kind": "skill", "skill_id": "pick_and_drop", "parameters": {"pill": f"pill_{pill:02d}", "arm": "left"}}
    from convoy_sim.bimanual_pill_task.planning import PlannerCall

    episode._on_skill_call(PlannerCall(1, "left", "skill", 0.0, {}, dict(decision), "ok"), 2.0)
    slot = episode.arms["left"]
    assert slot.held is not None and slot.skill is None and episode.rejected_decisions == 0
    episode._release_held("left", 3.0)  # still in use: keeps holding
    assert slot.held is not None and slot.skill is None
    episode.space.owner = None
    episode._release_held("left", 4.0)  # free: the model's decision starts unchanged
    assert slot.held is None and slot.skill is not None and slot.skill.pill == pill
    # A hold that runs out is a stale rejection, and the arm asks again at once.
    other = Episode(EpisodeSpec(CONFIGS["edge_qwen_edge_skills"], SLICES["nominal"], 0, 30.0), planner=_ScriptedTransport([]))
    other.space.owner = "right"
    other._on_skill_call(PlannerCall(1, "left", "skill", 0.0, {}, dict(decision), "ok"), 2.0)
    other._release_held("left", 2.0 + 6.0)
    assert other.arms["left"].held is None and other.arms["left"].skill is None and other.rejected_decisions == 1
    assert other.arms["left"].next_request_s == 8.0 and other.events[-1]["event"] == "decision_rejected"


def test_edge_qwen_needs_a_device_connection_and_has_no_stand_in():
    spec = EpisodeSpec(CONFIGS["edge_qwen_edge_skills"], SLICES["nominal"], 0, 5.0)
    with pytest.raises(ValueError, match="no stand-in"):
        Episode(spec)
    profile = CONFIGS["edge_qwen_edge_skills"].skill_planner
    assert profile.source == "device" and profile.latency is None and profile.concurrency == 1
    manifest = release_manifest(CONFIGS["edge_qwen_edge_skills"])["skill_planner"]
    assert manifest["latency"] == "measured per call (no latency model)"
    assert manifest["request"]["prompt_version"] == PROMPT_VERSION and manifest["request"]["max_tokens"] == MAX_TOKENS


def test_an_episode_executes_device_decisions_and_reports_only_measured_numbers(tmp_path):
    transport = _FirstAvailable([])
    out = tmp_path / "0000-nominal"
    frames = [np.zeros((16, 16, 3), np.uint8)]
    recorder = OfflineReplayRecorder(out, image_format="png", frame_source=lambda world: frames[0], max_steps=40)
    summary = run_episode(EpisodeSpec(CONFIGS["edge_qwen_edge_skills"], SLICES["nominal"], 0, 14.0), recorder,
                          planner=transport)
    device = summary["device_planner"]
    assert device["calls"] == len(transport.sent) >= 3 and device["by_result"] == {"valid": device["calls"]}
    assert summary["skill_attempts"] >= 1 and set(device["actions"]) <= {"pick_and_drop", "wait", "done"}
    assert summary["planner_latency_p50_ms"] == device["e2e_p50_ms"] == 1500.0
    assert device["device_p50_ms"] == 800.0 and summary["network_outage_s"] is None
    assert device["trace_ids"][0] == "tr_0001"
    # Calls are made one at a time; a later call starts after the previous one ended in sim time.
    starts = [r["sim_start_s"] for r in recorder_records(out)]
    ends = [r["sim_end_s"] for r in recorder_records(out)]
    assert all(s2 >= e1 for e1, s2 in zip(ends, starts[1:], strict=False))
    metrics = episode_metrics(summary)
    assert len(metrics) <= 32 and len(json.dumps(metrics)) <= 4096
    assert metrics["planner_calls"] == device["calls"] and metrics["planner_e2e_p50_ms"] == 1500.0
    assert "network_outage_s" not in metrics and "motor_policy_available" not in metrics
    replay = json.loads((out / "replay.json").read_text())
    assert replay["planner_ms"] == 1500.0 and replay["skill"].startswith("pick_and_drop ×")
    local = [json.loads(p.read_text()) for p in sorted((out / "frames").glob("*.json"))]
    assert any(frame.get("planner") for frame in local)  # per-step planner data stays in the local export


def recorder_records(out):
    """Every planner call recorded in the local frames of an offline export, in order."""
    rows = []
    for path in sorted((out / "frames").glob("*.json")):
        rows += json.loads(path.read_text()).get("planner", [])
    return [{"sim_start_s": r["started_s"], "sim_end_s": round(r["started_s"] + r["latency_ms"] / 1000, 3)} for r in rows]


def test_evaluate_runs_device_episodes_one_after_another_and_stops_when_the_device_goes_offline(tmp_path):
    from convoy_sim.bimanual_pill_task.cli import main
    from convoy_sim.bimanual_pill_task.evaluate import evaluate

    with pytest.raises(SystemExit, match="planner-server"):
        main(["evaluate", "--configs", "edge_qwen_edge_skills", "--output", str(tmp_path / "refused")])
    with pytest.raises(ValueError, match="no stand-in"):
        evaluate(tmp_path / "none", ["edge_qwen_edge_skills"], ["nominal"], [0])

    class GoesOffline(_FirstAvailable):
        def device(self):
            self.online = self.checks < 2  # online for the manifest and the first episode, then offline
            return super().device()

    transport = GoesOffline([])
    report = evaluate(tmp_path / "run", ["edge_qwen_edge_skills"], ["nominal"], [0, 1], horizon=6.0, planner=transport,
                      name="Pills to bottle · Edge Qwen · test")
    first, second = sorted(report["episodes"], key=lambda e: e["seed"])
    assert first["status"] == "completed" and first["device_planner"]["calls"] == len(transport.sent) >= 2
    assert second["status"] == "not_run" and "offline" in second["reason"] and report["not_run"] == [second]
    assert report["results"]["edge_qwen_edge_skills"]["overall"]["episodes"] == 1  # not-run episodes are not counted
    calls = (tmp_path / "run" / "edge_qwen_edge_skills" / "calls.jsonl").read_text().splitlines()
    assert len(calls) == len(transport.sent) and json.loads(calls[0])["seed"] == 0
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["device_planner"]["prompt_version"] == PROMPT_VERSION
    assert manifest["device_planner"]["device_at_start"]["release_id"] == "rel_test"


def test_an_unreadable_device_status_is_asked_again_before_it_stops_the_evaluation(monkeypatch):
    from convoy_sim.bimanual_pill_task import evaluate as evaluation

    monkeypatch.setattr(evaluation.time, "sleep", lambda seconds: None)

    class Flaky(_ScriptedTransport):
        def device(self):
            self.checks += 1
            if self.checks < 3:
                raise ConnectionResetError("reset")
            return super().device()

    flaky = Flaky([])
    assert evaluation._device_problem(flaky) is None and flaky.checks == 4  # two failures, then online

    class Down(_ScriptedTransport):
        def device(self):
            raise TimeoutError("timed out")

    assert evaluation._device_problem(Down([])) == "device status unreadable (TimeoutError)"
    assert evaluation._device_problem(_ScriptedTransport([], online=False)).startswith("device offline")
