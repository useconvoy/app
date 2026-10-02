"""The cloud vision planner (the Cloud GPT-6 Luna (vision) configuration): pixel geometry, the strict parser, the
executive's check, the failure policy, the spend cap and the Responses API client. HTTP is mocked: no test reaches
the API, and no key is needed (the client is given a fake one)."""

import io
import json
import math
import urllib.error

import numpy as np
import pytest

from convoy_sim.bimanual_pill_task import physics as P
from convoy_sim.bimanual_pill_task.configs import (
    CONFIGS,
    SLICES,
    EpisodeSpec,
    release_manifest,
    vision_labels,
)
from convoy_sim.bimanual_pill_task.head_camera import (
    RENDER_SIZE,
    WINDOW,
    Frame,
    Intrinsics,
    back_project,
    finger_obstruction,
    grasp_axis,
    project,
    with_ticks,
)
from convoy_sim.bimanual_pill_task.offline_replay import episode_metrics, vision_metrics, write_evaluation
from convoy_sim.bimanual_pill_task.openai_responses import (
    OpenAIResponsesClient,
    ResponseOutcome,
    SpendCapReached,
    SpendLedger,
    cost_usd,
    worst_case_usd,
)
from convoy_sim.bimanual_pill_task.vision_planner import (
    PROMPT_VERSION,
    REPLY_SCHEMA,
    Action,
    ArmState,
    FailedSpot,
    ReplyError,
    Target,
    VisionFailurePolicy,
    VisionPlannerEndpoint,
    VisionRequest,
    build_content,
    check_point,
    choose_finger_yaw,
    instruction,
    parse_reply,
    reach_columns,
)

FAKE_KEY = "sk-test-" + "k" * 40
# The head camera's pose at rest (robot.py: head tilt 0.35 rad, camera pitched 0.55 rad inside the head).
CAMERA_POS = np.array([0.10748, 0.0, 1.25488])
CAMERA_ROT = np.array([[0.0, 0.78333, -0.62161], [-1.0, 0.0, 0.0], [0.0, 0.62161, 0.78333]])
TABLE_Z = P.MAT_TOP_M


# ---- pixel geometry -------------------------------------------------------------------------------------


def test_back_projection_with_known_intrinsics():
    k = Intrinsics(fx=100.0, fy=100.0, cx=50.0, cy=40.0, width=100, height=80)
    looking_down = np.diag([1.0, 1.0, 1.0])  # camera axes = world axes: it looks along -z
    # The pixel whose centre is the principal point sees straight ahead: depth 2 m -> (0, 0, -2).
    assert np.allclose(back_project(49.5, 39.5, 2.0, k, [0, 0, 0], looking_down), [0, 0, -2])
    # Pixel (59, 39): centre 10 px right of the principal point -> x = 10 / 100 * depth; image down is -y.
    assert np.allclose(back_project(59, 39, 2.0, k, [1, 2, 3], looking_down), [1 + 0.2 - 0.005 * 2, 2 + 0.005 * 2, 1])
    assert np.allclose(back_project(49, 49, 1.0, k, [0, 0, 0], looking_down), [-0.005, -0.095, -1.0])
    # project() inverts it: continuous coordinates of the point are the pixel centre.
    point = back_project(17, 63, 1.3, k, CAMERA_POS, CAMERA_ROT)
    assert np.allclose(project(point, k, CAMERA_POS, CAMERA_ROT), (17.5, 63.5))
    assert project(CAMERA_POS + CAMERA_ROT @ np.array([0, 0, 1.0]), k, CAMERA_POS, CAMERA_ROT) is None  # behind


def test_window_intrinsics_follow_mujocos_camera_model():
    full = Intrinsics.from_fovy(64.0, RENDER_SIZE)
    window = Intrinsics.from_fovy(64.0, RENDER_SIZE, WINDOW)
    assert full.fy == pytest.approx(384 / math.tan(math.radians(32)))  # vertical fov over the full height
    assert window.fx == full.fx and (window.width, window.height) == (512, 384)
    assert (window.cx, window.cy) == (full.cx - 256, full.cy - 208)  # the window keeps the focal length
    # A world point lands on the same scene pixel in the render and in the window, offset by the window origin.
    point = [0.5, 0.1, TABLE_Z]
    u_full, v_full = project(point, full, CAMERA_POS, CAMERA_ROT)
    u_win, v_win = project(point, window, CAMERA_POS, CAMERA_ROT)
    assert (u_full - u_win, v_full - v_win) == pytest.approx((256, 208))


def _table_frame(raised=()):
    """A synthetic head-camera window looking at a flat table, with optional raised boxes
    (centre xy, length, width, yaw, height): depth by ray casting, so it is exact."""
    k = Intrinsics.from_fovy(64.0, RENDER_SIZE, WINDOW)
    v, u = np.mgrid[0:k.height, 0:k.width]
    rays = np.stack([(u + 0.5 - k.cx) / k.fx, -(v + 0.5 - k.cy) / k.fy, -np.ones_like(u, dtype=float)], axis=-1)
    world = rays @ CAMERA_ROT.T
    depth = (TABLE_Z - CAMERA_POS[2]) / world[..., 2]
    for (cx, cy), length, width, yaw, height in raised:
        d_top = (TABLE_Z + height - CAMERA_POS[2]) / world[..., 2]
        top = CAMERA_POS + world * d_top[..., None]
        along = (top[..., 0] - cx) * math.cos(yaw) + (top[..., 1] - cy) * math.sin(yaw)
        across = -(top[..., 0] - cx) * math.sin(yaw) + (top[..., 1] - cy) * math.cos(yaw)
        inside = (np.abs(along) <= length / 2) & (np.abs(across) <= width / 2)
        depth = np.where(inside, d_top, depth)
    rgb = np.zeros((k.height, k.width, 3), np.uint8)
    return Frame(rgb, depth.astype(np.float32), k, CAMERA_POS, CAMERA_ROT, 0.0)


def _pixel(frame, xyz):
    u, v = project(xyz, frame.intrinsics, frame.position, frame.rotation)
    return int(math.floor(u)), int(math.floor(v))


def test_a_pointed_pixel_back_projects_onto_the_table_through_the_frames_depth():
    frame = _table_frame()
    for xy in ((0.45, 0.12), (0.55, -0.20), (0.40, 0.0)):
        u, v = _pixel(frame, [*xy, TABLE_Z])
        point = frame.point(u, v)
        assert point[2] == pytest.approx(TABLE_Z, abs=1e-6)
        assert np.linalg.norm(point[:2] - np.array(xy)) < 0.0025  # within the pixel's footprint (~1 mm)
    assert frame.point(-1, 10) is None and frame.point(512, 10) is None and frame.point(10, 384) is None


def test_the_grasp_axis_is_the_long_axis_of_the_raised_blob_under_the_pixel():
    pill = ((0.50, 0.10), 0.020, 0.008, math.radians(30), 0.008)
    frame = _table_frame(raised=[pill])
    u, v = _pixel(frame, [0.50, 0.10, TABLE_Z + 0.008])
    axis = grasp_axis(frame, u, v, TABLE_Z)
    assert abs(math.remainder(axis - math.radians(30), math.pi)) < math.radians(4)
    assert grasp_axis(frame, *_pixel(frame, [0.45, -0.10, TABLE_Z]), TABLE_Z) is None  # bare mat: no blob
    tall = _table_frame(raised=[((0.50, 0.10), 0.06, 0.06, 0.0, 0.11)])  # the bottle is far above pill height
    assert grasp_axis(tall, *_pixel(tall, [0.50, 0.10, TABLE_Z + 0.11]), TABLE_Z) is None


def test_ticks_mark_the_borders_and_leave_the_scene_alone():
    image = np.full((384, 512, 3), 30, np.uint8)
    ticked = with_ticks(image)
    assert ticked.shape == image.shape
    changed = np.any(ticked != image, axis=-1)
    assert changed[:, :20].any() and changed[:20, :].any() and changed[-20:, :].any() and changed[:, -40:].any()
    assert not changed[30:354, 50:460].any()  # nothing is drawn over the scene


# ---- the request and the strict parser ---------------------------------------------------------------------


def _arms(right_busy=None, right_target=None, right_px=None, left_last=None):
    return {
        "left": ArmState((0.08, 0.17), ((0.20, 0.25), (0.30, 0.30), (0.33, 0.30)), last_result=left_last),
        "right": ArmState((0.08, -0.17), ((0.20, -0.25), (0.30, -0.30), (0.33, -0.30)), busy=right_busy,
                          target_xy=right_target, target_px=right_px),
    }


def _request(side="left", frame=None, **arms):
    return VisionRequest(side, frame or _table_frame(), _arms(**arms))


def test_the_request_is_the_image_and_a_short_instruction_with_no_scene_state():
    request = _request(right_busy="picking", right_target=(0.47, -0.12), right_px=(300, 150),
                       left_last="the fingers closed on nothing at (200, 140)")
    text = instruction(request, feedback="Your previous reply \"x\" was refused: because. Reply again with one JSON object.")
    assert "Arm L is free" in text and "Arm R is picking up the pill at (300, 150)" in text
    assert "Arm L's last pick: the fingers closed on nothing at (200, 140)." in text
    assert "512 x 384 pixels" in text and text.endswith("Reply again with one JSON object.")
    assert "0.47" not in text and "pill_" not in text  # no positions in metres, no pill ids
    content = build_content(text, b"\xff\xd8jpeg\xff\xd9")
    assert [part["type"] for part in content] == ["input_text", "input_image"]
    assert content[1]["image_url"].startswith("data:image/jpeg;base64,") and content[1]["detail"] == "high"
    assert REPLY_SCHEMA["strict"] is True and REPLY_SCHEMA["schema"]["required"] == ["arm", "action", "u", "v"]


@pytest.mark.parametrize("text, action", [
    ('{"arm": "L", "action": "pick", "u": 212, "v": 140}', Action("L", "pick", 212, 140)),
    (' \n{"arm":"R","action":"wait","u":null,"v":null}\n', Action("R", "wait")),
    ('{"action": "done", "arm": "L", "v": null, "u": null}', Action("L", "done")),
])
def test_the_parser_accepts_exactly_the_declared_shapes(text, action):
    assert parse_reply(text) == action


@pytest.mark.parametrize("text, kind", [
    (None, "invalid_json"), ("", "invalid_json"),
    ('```json\n{"arm": "L", "action": "wait", "u": null, "v": null}\n```', "invalid_json"),
    ('Sure: {"arm": "L", "action": "pick", "u": 1, "v": 2}', "invalid_json"),
    ('{"arm": "L", "action": "pick", "u": 1, "v": 2', "invalid_json"),  # cut off
    ('{"arm": "L", "arm": "R", "action": "wait", "u": null, "v": null}', "invalid_json"),  # duplicate key
    ('{"arm": "L", "action": "pick", "u": NaN, "v": 2}', "invalid_json"),
    ('[{"arm": "L", "action": "wait", "u": null, "v": null}]', "invalid_schema"),
    ('{"arm": "L", "action": "pick", "u": 1}', "invalid_schema"),  # missing key
    ('{"arm": "L", "action": "pick", "u": 1, "v": 2, "pill": 3}', "invalid_schema"),  # extra key
    ('{"arm": "left", "action": "pick", "u": 1, "v": 2}', "invalid_schema"),
    ('{"arm": "L", "action": "grab", "u": 1, "v": 2}', "invalid_schema"),
    ('{"arm": "L", "action": "pick", "u": 1.5, "v": 2}', "invalid_schema"),
    ('{"arm": "L", "action": "pick", "u": true, "v": 2}', "invalid_schema"),
    ('{"arm": "L", "action": "pick", "u": null, "v": null}', "invalid_schema"),
    ('{"arm": "L", "action": "wait", "u": 1, "v": 2}', "invalid_schema"),
])
def test_the_parser_refuses_everything_else_without_repairing_it(text, kind):
    with pytest.raises(ReplyError) as refused:
        parse_reply(text)
    assert refused.value.kind == kind


# ---- the executive's check: depth and the robot's own geometry -------------------------------------------


def test_a_pick_is_checked_on_the_frames_depth_and_the_arms_geometry():
    pill = ((0.50, 0.10), 0.020, 0.008, 0.0, 0.008)
    bottle = ((0.37, 0.0), 0.065, 0.065, 0.0, 0.11)
    frame = _table_frame(raised=[pill, bottle])
    request = _request(frame=frame, right_busy="picking", right_target=(0.47, -0.05), right_px=(300, 150))
    u, v = _pixel(frame, [0.50, 0.10, TABLE_Z + 0.008])
    target = check_point(Action("L", "pick", u, v), request)
    assert isinstance(target, Target) and target.pixel == (u, v)
    assert target.point[2] == pytest.approx(TABLE_Z + 0.008, abs=1e-4)  # the visible top of the pill
    assert abs(math.remainder(target.finger_yaw - math.pi / 2, math.pi)) < math.radians(5)  # across the long axis

    def refused(u, v, side="left", arm="L"):
        result = check_point(Action(arm, "pick", u, v), _request(side=side, frame=frame, right_busy="picking",
                                                                 right_target=(0.47, -0.05), right_px=(300, 150)))
        return result[0] if isinstance(result, tuple) else result

    assert refused(600, 100) == "outside_image" and refused(-3, 100) == "outside_image"
    assert refused(*_pixel(frame, [0.37, 0.0, TABLE_Z + 0.11])) == "not_on_mat"  # the bottle's top
    assert refused(*_pixel(frame, [0.50, -0.20, TABLE_Z])) == "out_of_reach"  # the right half, for arm L
    assert refused(*_pixel(frame, [0.47, 0.06, TABLE_Z])) == "next_to_other_arm"  # 11 cm from arm R's target
    assert refused(u, v, arm="R") == "wrong_arm"
    assert check_point(Action("L", "wait"), request) is None and check_point(Action("L", "done"), request) is None


def test_a_spot_where_two_picks_failed_is_given_up_and_named_in_the_request():
    frame = _table_frame(raised=[((0.50, 0.10), 0.020, 0.008, 0.0, 0.008)])
    u, v = _pixel(frame, [0.50, 0.10, TABLE_Z + 0.008])
    once = VisionRequest("left", frame, _arms(), failed_spots=(FailedSpot((u, v), (0.497, 0.10), 1),))
    assert isinstance(check_point(Action("L", "pick", u, v), once), Target)  # one failure: still allowed
    assert "do not pick there" not in instruction(once)
    twice = VisionRequest("left", frame, _arms(), failed_spots=(FailedSpot((u + 1, v), (0.497, 0.10), 2),))
    assert check_point(Action("L", "pick", u, v), twice)[0] == "given_up"
    assert f"Picks failed twice at ({u + 1}, {v}): do not pick there again." in instruction(twice)
    far = _pixel(frame, [0.47, 0.15, TABLE_Z])
    assert isinstance(check_point(Action("L", "pick", *far), twice), Target)  # 5 cm away: another spot


def test_the_finger_yaw_turns_away_from_a_neighbour_seen_in_the_depth():
    pill = ((0.50, 0.10), 0.020, 0.008, 0.0, 0.008)  # long axis along x: the fingers close along y
    alone = _table_frame(raised=[pill])
    u, v = _pixel(alone, [0.50, 0.10, TABLE_Z + 0.008])
    point = alone.point(u, v)
    yaw, seen, blocked = choose_finger_yaw(alone, u, v, point, (0.08, 0.17), TABLE_Z)
    assert seen and blocked == 0 and abs(math.remainder(yaw - math.pi / 2, math.pi)) < math.radians(5)
    # Something small under the edge of the +y finger: that yaw is obstructed, a turned one comes down on clear mat.
    bead = ((0.509, 0.1135), 0.004, 0.004, 0.0, 0.008)
    crowded = _table_frame(raised=[pill, bead])
    point = crowded.point(u, v)
    assert finger_obstruction(crowded, u, v, point[:2], math.pi / 2, TABLE_Z) > 0
    yaw, seen, blocked = choose_finger_yaw(crowded, u, v, point, (0.08, 0.17), TABLE_Z)
    assert blocked == 0 and abs(math.remainder(yaw - math.pi / 2, math.pi)) >= 0.2 - 1e-9
    # A parallel neighbour 15 mm away leaves no clear yaw within 0.6 rad: the least obstructed one is kept.
    stuck = _table_frame(raised=[pill, ((0.50, 0.115), 0.020, 0.008, 0.0, 0.008)])
    assert choose_finger_yaw(stuck, u, v, stuck.point(u, v), (0.08, 0.17), TABLE_Z)[2] > 0


def test_the_arms_halves_of_the_image_come_from_the_calibration():
    columns = reach_columns(_table_frame(), TABLE_Z)
    # y = -2 cm (arm L's limit) is right of the image's centre column, y = +2 cm (arm R's) left of it.
    assert 256 < columns["left"] < 285 and 227 < columns["right"] < 256
    text = instruction(_request())
    assert f"left of about column {columns['left']}" in text and f"right of about column {columns['right']}" in text


# ---- the failure policy (scripted transport) ---------------------------------------------------------------


class _Scripted:
    """Answers from a script: a reply text, or a ResponseOutcome field dict (a failure)."""

    transport, provider, model, effort = "scripted-test", "openai", "gpt-6-luna", "low"

    def __init__(self, script, e2e_ms=2000.0):
        self.script, self.e2e_ms = list(script), e2e_ms
        self.sent: list[list[dict]] = []

    def send(self, content, *, text_format=None, label=""):
        assert text_format == REPLY_SCHEMA
        self.sent.append(content)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        base = {"e2e_ms": self.e2e_ms, "sent_at": "t0", "finished_at": "t1"}
        if isinstance(item, dict):
            return ResponseOutcome(**{**base, "status": "http_error", **item})
        return ResponseOutcome(**base, status="completed", http_status=200, text=item, model="gpt-6-luna",
                               input_tokens=600, output_tokens=140, reasoning_tokens=110,
                               cost_usd=cost_usd({"input_tokens": 600, "output_tokens": 140}))


FRAME = _table_frame(raised=[((0.50, 0.10), 0.020, 0.008, 0.0, 0.008)])
PILL_PX = _pixel(FRAME, [0.50, 0.10, TABLE_Z + 0.008])


def _endpoint(script, **kwargs):
    transport = _Scripted(script, **kwargs)
    seen = []
    endpoint = VisionPlannerEndpoint(CONFIGS["cloud_luna_vision"].skill_planner, transport,
                                     lambda side: _request(side=side, frame=FRAME), 24, on_record=seen.append)
    return endpoint, transport, seen


def _run(endpoint, t0=0.0, until=200.0, dt=0.01):
    t = t0
    while t < until:
        done = endpoint.poll(t)
        if done:
            return done, t
        t = round(t + dt, 2)
    raise AssertionError("no decision")


def _pick(u, v, arm="L"):
    return json.dumps({"arm": arm, "action": "pick", "u": u, "v": v})


def test_a_valid_pick_is_delivered_after_its_measured_round_trip():
    endpoint, transport, seen = _endpoint([_pick(*PILL_PX)])
    call = endpoint.submit("left", {}, 0.0)
    (delivered,), t = _run(endpoint)
    assert delivered is call and t == pytest.approx(2.0, abs=0.011)  # the arm held for the 2.0 s round trip
    params = call.decision["parameters"]
    assert call.decision["kind"] == "skill" and call.decision["skill_id"] == "pick_at_point"
    assert params["arm"] == "left" and params["pixel"] == list(PILL_PX)
    assert np.linalg.norm(np.array(params["point"][:2]) - [0.50, 0.10]) < 0.006
    record = endpoint.records[0]
    assert record["result"] == "valid" and record["next"] == "delivered" and record["prompt_version"] == PROMPT_VERSION
    assert record["image"]["bytes"] > 1000 and len(record["image"]["sha256"]) == 64 and record["cost_usd"] > 0
    assert record["text"] == _pick(*PILL_PX) and "Arm L is free" in record["instruction"]
    assert seen[0]["status"] == "ok" and seen[0]["latency_ms"] == 2000.0


def test_refused_replies_are_re_asked_with_the_reason_then_counted_as_a_failed_decision():
    bottle_px = _pixel(FRAME, [0.70, -0.25, TABLE_Z])  # arm R's half
    endpoint, transport, _ = _endpoint(['```{"arm": "L"}```', _pick(*bottle_px), _pick(700, 10)])
    endpoint.submit("left", {}, 0.0)
    (delivered,), t = _run(endpoint)
    assert delivered.decision["kind"] == "failed"
    assert [r["result"] for r in endpoint.records] == ["invalid_json", "invalid_choice", "invalid_choice"]
    assert [r["refusal_code"] for r in endpoint.records] == ["invalid_json", "out_of_reach", "outside_image"]
    assert [r["next"] for r in endpoint.records] == ["re-asked", "re-asked", "failed_decision"]
    assert t == pytest.approx(6.0, abs=0.04)  # three measured round trips
    second = transport.sent[1][0]["text"]
    assert "was refused: the JSON object must not be wrapped in a ``` code block" in second
    assert "is out of reach of arm L" in transport.sent[2][0]["text"]
    stats = endpoint.stats(t)
    assert stats["failed_decisions"] == 1 and stats["decision_counts"] == {
        "resolved": 1, "first_call_accepted": 0, "reasked": 1, "failed": 1}


def test_transport_failures_are_re_asked_without_feedback_and_a_refused_key_stops_the_episode():
    endpoint, transport, _ = _endpoint([{"http_status": 503, "e2e_ms": 300.0},
                                        {"status": "timeout", "http_status": None, "e2e_ms": 60000.0},
                                        json.dumps({"arm": "L", "action": "wait", "u": None, "v": None})])
    endpoint.submit("left", {}, 0.0)
    (delivered,), t = _run(endpoint, until=100.0)
    assert delivered.decision == {"kind": "wait"}
    assert [r["result"] for r in endpoint.records] == ["http_error", "timeout", "valid"]
    assert t == pytest.approx(0.3 + 60.0 + 2.0, abs=0.04)  # a timeout costs its whole wait in sim time
    assert all("was refused" not in sent[0]["text"] for sent in transport.sent)

    refused, transport, _ = _endpoint([{"http_status": 401, "e2e_ms": 200.0}])
    refused.submit("left", {}, 0.0)
    (stopped,), _ = _run(refused)
    assert stopped.decision == {"kind": "stop", "reason": "access_refused"} and refused.stopped == "access_refused"
    later = refused.submit("right", {}, 1.0)
    assert _run(refused, t0=1.0)[0] == [later] and len(transport.sent) == 1  # nothing more is sent


def test_the_spend_cap_and_the_call_budget_stop_the_episode():
    endpoint, transport, _ = _endpoint([SpendCapReached("over the cap")])
    endpoint.submit("left", {}, 0.0)
    (stopped,), _ = _run(endpoint)
    assert stopped.decision == {"kind": "stop", "reason": "spend_cap_reached"} and not endpoint.records
    policy = VisionFailurePolicy()
    assert policy.budget(24) == 84 and policy.budget(30) == 102
    endpoint, transport, _ = _endpoint([_pick(700, 10)] * 3)
    endpoint.budget = 2
    endpoint.submit("left", {}, 0.0)
    (stopped,), _ = _run(endpoint)
    assert stopped.decision == {"kind": "stop", "reason": "planner_call_budget_exhausted"} and len(transport.sent) == 2


def test_both_arms_can_have_a_call_in_flight_at_once():
    endpoint, transport, _ = _endpoint([json.dumps({"arm": "L", "action": "wait", "u": None, "v": None}),
                                        json.dumps({"arm": "R", "action": "wait", "u": None, "v": None})])
    left = endpoint.submit("left", {}, 0.0)
    right = endpoint.submit("right", {}, 0.5)
    delivered, t = _run(endpoint)
    assert delivered == [left, right] and t == pytest.approx(2.0, abs=0.011)  # both started at the first poll
    assert [r["arm"] for r in endpoint.records] == ["L", "R"] and endpoint.pending("left") is False


# ---- the spend cap and the client (mocked HTTP) -------------------------------------------------------------


class _Response:
    def __init__(self, status, body, headers=None):
        self.status, self._body, self.headers = status, json.dumps(body).encode(), headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    """The API, scripted: (status, body, headers) per request, or an exception to raise."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []

    def open(self, request, timeout):
        self.requests.append(request)
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        status, body, headers = item
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "error", headers, io.BytesIO(json.dumps(body).encode()))
        return _Response(status, body, headers)


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        self.now += 0.75  # every reading advances: a call spans two readings
        return self.now


def _completed(text, input_tokens=600, cached=0, output_tokens=140, reasoning=110):
    return (200, {"id": "resp_test", "status": "completed", "model": "gpt-6-luna",
                  "output": [{"type": "reasoning", "summary": []},
                             {"type": "message", "content": [{"type": "output_text", "text": text}]}],
                  "usage": {"input_tokens": input_tokens, "input_tokens_details": {"cached_tokens": cached},
                            "output_tokens": output_tokens, "output_tokens_details": {"reasoning_tokens": reasoning}}},
            {"x-request-id": "req_test", "openai-processing-ms": "3203"})


def _client(tmp_path, replies, cap=3.0, **kwargs):
    opener = _Opener(replies)
    client = OpenAIResponsesClient(SpendLedger(tmp_path / "spend.jsonl", cap), key=FAKE_KEY, opener=opener,
                                   clock=_Clock(), wall=lambda: 1_790_000_000.0, sleep=lambda s: None, **kwargs)
    return client, opener


def test_the_price_of_a_response_and_the_worst_case_of_a_call():
    assert cost_usd({"input_tokens": 1_000_000, "output_tokens": 0}) == pytest.approx(0.10)
    assert cost_usd({"input_tokens": 1_000_000, "input_tokens_details": {"cached_tokens": 1_000_000}}) == pytest.approx(0.01)
    assert cost_usd({"output_tokens": 1_000_000, "output_tokens_details": {"reasoning_tokens": 900_000}}) == pytest.approx(0.50)
    assert cost_usd({"input_tokens": 599, "output_tokens": 138}) == pytest.approx(0.0001289)  # the measured first call
    assert worst_case_usd(6000, 2048) == pytest.approx(0.0006 + 0.001024)


def test_the_client_sends_the_declared_request_measures_it_and_records_its_cost(tmp_path):
    client, opener = _client(tmp_path, [_completed('{"arm":"L","action":"wait","u":null,"v":null}')])
    result = client.send([{"type": "input_text", "text": "hi"}], text_format=REPLY_SCHEMA, label="dev call 1")
    assert result.status == "completed" and result.text == '{"arm":"L","action":"wait","u":null,"v":null}'
    assert result.e2e_ms == 750.0 and result.processing_ms == 3203.0 and result.request_id == "req_test"
    assert (result.input_tokens, result.output_tokens, result.reasoning_tokens) == (600, 140, 110)
    sent = json.loads(opener.requests[0].data)
    assert sent["model"] == "gpt-6-luna" and sent["reasoning"] == {"effort": "low"} and sent["store"] is False
    assert sent["max_output_tokens"] == 2048 and sent["text"] == {"format": REPLY_SCHEMA}
    assert opener.requests[0].full_url == "https://api.openai.com/v1/responses"
    assert opener.requests[0].get_header("Authorization") == f"Bearer {FAKE_KEY}"
    (line,) = SpendLedger(tmp_path / "spend.jsonl").entries()
    assert line["label"] == "dev call 1" and line["cost_usd"] == pytest.approx(0.00013) and line["cost_basis"] == "usage"
    assert line["cumulative_usd"] == pytest.approx(0.00013) and line["input_tokens"] == 600
    # The key is in the request header only: never in the ledger, the result or the client's repr.
    assert FAKE_KEY not in (tmp_path / "spend.jsonl").read_text() and FAKE_KEY not in repr(client)
    assert FAKE_KEY not in json.dumps(result.__dict__)


def test_the_spend_cap_refuses_a_call_that_could_pass_it_before_anything_is_sent(tmp_path):
    worst = worst_case_usd(6000, 2048)
    client, opener = _client(tmp_path, [_completed("{}")] * 3, cap=0.0002 + worst)
    client.send([], label="first")  # 0.00013 spent
    assert client.ledger.spent_usd == pytest.approx(0.00013)
    client.send([], label="second")  # 0.00026: still allowed (0.00013 + worst <= cap)
    with pytest.raises(SpendCapReached):
        client.send([], label="third")  # 0.00026 + worst > cap: refused
    assert len(opener.requests) == 2 and client.ledger.booked_usd == 0  # nothing sent, nothing left booked
    # The total is read back from the file: a new client on the same ledger is refused too.
    again, opener = _client(tmp_path, [_completed("{}")], cap=0.0002 + worst)
    with pytest.raises(SpendCapReached):
        again.send([])
    assert opener.requests == []
    # The worst case grows with the largest input seen.
    big, _ = _client(tmp_path / "big", [_completed("{}", input_tokens=10_000)])
    big.send([])
    assert big.worst_case_usd() == pytest.approx(worst_case_usd(15_000, 2048))


def test_failed_calls_are_priced_as_the_api_bills_them(tmp_path):
    client, _ = _client(tmp_path, [(400, {"error": {"code": "invalid_value", "message": "bad"}}, {}),
                                   (429, {"error": {"type": "rate_limit"}}, {"retry-after": "7"}),
                                   urllib.error.URLError(TimeoutError("timed out")),
                                   ConnectionResetError("reset")])
    bad = client.send([])
    limited = client.send([])
    timeout = client.send([])
    reset = client.send([])
    assert (bad.status, bad.http_status, bad.error_code, bad.cost_usd) == ("http_error", 400, "invalid_value", 0.0)
    assert limited.retry_after_s == 7.0 and limited.cost_basis == "not_billed"
    worst = worst_case_usd(6000, 2048)
    assert timeout.status == "timeout" and timeout.cost_usd == pytest.approx(worst) and timeout.cost_basis == "worst_case"
    assert reset.status == "transport_error" and reset.cost_usd == pytest.approx(worst)
    assert client.ledger.spent_usd == pytest.approx(2 * worst)  # no response: the API may have run it


def test_the_client_needs_a_key_and_reads_it_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("OPEN_AI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPEN_AI_API_KEY"):
        OpenAIResponsesClient(SpendLedger(tmp_path / "spend.jsonl"))
    monkeypatch.setenv("OPEN_AI_API_KEY", FAKE_KEY)
    client = OpenAIResponsesClient(SpendLedger(tmp_path / "spend.jsonl"))
    assert FAKE_KEY not in repr(client) and FAKE_KEY not in json.dumps(client.settings())


# ---- configuration, labels and the export -------------------------------------------------------------------


def test_the_vision_configuration_has_no_stand_in_and_records_its_settings():
    from convoy_sim.bimanual_pill_task.episode import Episode, make_episode

    config = CONFIGS["cloud_luna_vision"]
    assert config.label == "Cloud GPT-6 Luna (vision)" and config.skill_planner.source == "vision"
    assert config.skill_planner.latency is None and config.skill_planner.concurrency == 2
    spec = EpisodeSpec(config, SLICES["nominal"], 0, 5.0)
    with pytest.raises(ValueError, match="no stand-in"):
        Episode(spec)
    with pytest.raises(ValueError, match="no stand-in"):
        make_episode(spec)
    manifest = release_manifest(config)["skill_planner"]
    assert manifest["latency"] == "measured per call (no latency model)"
    request = manifest["request"]
    assert request["prompt_version"] == PROMPT_VERSION and request["client"]["model"] == "gpt-6-luna"
    assert request["client"]["reasoning_effort"] == "low" and request["policy"]["calls_per_decision"] == 3


def test_labels_and_metrics_name_the_model_and_fit_the_import(tmp_path):
    config = CONFIGS["cloud_luna_vision"]
    labels = vision_labels(config, "gpt-6-luna", "openai", "low", "openai-responses from cloud container")
    assert all(0 < len(value) <= 120 for value in labels.values())
    assert labels["config_label"] == "Edge: none (Jetson as thin edge) · Cloud: gpt-6-luna (openai, low effort)"
    path = write_evaluation(tmp_path, config, "Pills to bottle · nominal (seeds 0–4)", planner={
        "vision": True, "model": "gpt-6-luna", "provider": "openai", "effort": "low",
        "transport": "openai-responses from cloud container"})
    written = json.loads(path.read_text())
    assert "gpt-6-luna" in written["name"] and "openai-responses from cloud container" in written["policy_label"]
    endpoint, _, _ = _endpoint([_pick(*PILL_PX)])
    endpoint.submit("left", {}, 0.0)
    _run(endpoint)
    summary = {"pills": 24, "placed": 3, "fraction_placed": 0.125, "slice": "nominal", "outcome": "horizon",
               "protective_stops": 0, "arm_arm_contacts": 0, "max_bottle_tilt_deg": 0.1,
               "grasps": {"picks": 5, "empty": 1, "blocked": 1, "pills_lifted": 3, "picks_placed": 3},
               "vision_planner": endpoint.stats(10.0)}
    metrics = episode_metrics(summary)
    assert metrics == vision_metrics(summary)
    assert len(metrics) <= 32 and len(json.dumps(metrics, ensure_ascii=False, separators=(",", ":")).encode()) <= 4096
    assert metrics["planner_decisions"] == metrics["planner_first_call_accepted"] == 1
    assert metrics["grasps_empty"] == 1 and metrics["pills_grasped"] == 3 and metrics["planner_cost_usd"] > 0
    assert metrics["planner_tokens_in"] == 600 and metrics["planner_tokens_out"] == 140
    assert metrics["measurement_source"].startswith("gpt-6-luna (openai, low effort) via scripted-test")


# ---- whole episodes (MuJoCo rendering: MUJOCO_GL=egl or osmesa) -------------------------------------------------


class _Pointer(_Scripted):
    """A test double that points where the simulator says the nearest reachable pill is: the planner's model is
    replaced, and every other part of the episode (camera, depth, checks, grasp, physics) is the real one."""

    def __init__(self):
        super().__init__([], e2e_ms=1500.0)
        self.episode = None

    def send(self, content, *, text_format=None, label=""):
        from convoy_sim.bimanual_pill_task.vision_planner import reaches

        self.sent.append(content)
        side = "left" if "Arm L is free" in content[0]["text"] else "right"
        episode, me = self.episode, "L" if side == "left" else "R"
        frame = episode.camera.capture()
        other = episode.arms["right" if side == "left" else "left"]
        busy = [other.skill.target_xy] if other.skill is not None and not other.skill.done else []
        options = []
        for view in episode.world.pills():
            shoulder = episode.arms[side].controller.kin.shoulder_pos[:2]
            far = all(np.linalg.norm(view.pos[:2] - p) > 0.13 for p in [*other.controller.links_xy()[1:], *busy])
            if view.on_mat and far and reaches(shoulder, side, view.pos[:2]):
                options.append((float(np.linalg.norm(view.pos[:2] - episode.arms[side].controller.pos[:2])), view))
        reply = {"arm": me, "action": "wait", "u": None, "v": None}
        if options:
            u, v = project(min(options, key=lambda o: o[0])[1].pos, frame.intrinsics, frame.position, frame.rotation)
            reply = {"arm": me, "action": "pick", "u": int(u), "v": int(v)}
        base = {"e2e_ms": self.e2e_ms, "sent_at": "t0", "finished_at": "t1"}
        return ResponseOutcome(**base, status="completed", http_status=200, text=json.dumps(reply), model="gpt-6-luna",
                               input_tokens=600, output_tokens=150, reasoning_tokens=120, cost_usd=0.000135)


def test_an_episode_points_at_pixels_grasps_there_and_exports_measured_numbers(tmp_path):
    from convoy_sim.bimanual_pill_task.episode import make_episode
    from convoy_sim.bimanual_pill_task.offline_replay import OfflineReplayRecorder

    pointer = _Pointer()
    out = tmp_path / "0000-nominal"
    recorder = OfflineReplayRecorder(out, image_format="png", frame_source=lambda world: np.zeros((16, 16, 3), np.uint8),
                                     max_steps=40)
    episode = make_episode(EpisodeSpec(CONFIGS["cloud_luna_vision"], SLICES["nominal"], 0, 12.0), recorder, pointer,
                           image_dir=tmp_path / "images")
    pointer.episode = episode
    summary = episode.run()
    vision, grasps = summary["vision_planner"], summary["grasps"]
    assert vision["calls"] == len(pointer.sent) >= 2 and vision["by_result"] == {"valid": vision["calls"]}
    assert grasps["picks"] >= 2 and grasps["picks_placed"] >= 1 and summary["placed"] >= 1
    assert summary["planner_latency_p50_ms"] == vision["e2e_p50_ms"] == 1500.0 and summary["network_outage_s"] is None
    first = episode.skill_planner.records[0]
    assert first["image"]["file"] == "0001.jpg" and (tmp_path / "images" / "0001.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert first["target"]["axis_seen"] is True and first["target"]["obstruction"] == 0
    replay = json.loads((out / "replay.json").read_text())
    metrics = replay["metrics"]
    assert len(metrics) <= 32 and metrics["pills_placed"] == summary["placed"] and metrics["grasp_attempts"] >= 2
    assert replay["skill"].startswith("pick_at_point ×") and replay["planner_ms"] == 1500.0


def test_two_arms_holding_pills_do_not_deadlock_on_the_bottle_zone():
    """A development run's opening (nominal seed 1000): both arms lift a pill at once, each after a pick whose
    shoulder-to-spot line crosses the bottle zone. Once an arm holds its pill the spot it left must stop counting
    as where it works (as PickAndDrop's target pill moves with the gripper), or each waits for the other forever."""
    from convoy_sim.bimanual_pill_task.episode import make_episode

    class Opening(_Scripted):
        replies = {"L": [(_pick(189, 128, "L"), 4560.0)], "R": [(_pick(315, 124, "R"), 2920.0)]}

        def send(self, content, *, text_format=None, label=""):
            me = "L" if "Arm L is free" in content[0]["text"] else "R"
            text, self.e2e_ms = (self.replies[me].pop(0) if self.replies[me]
                                 else (json.dumps({"arm": me, "action": "done", "u": None, "v": None}), 1000.0))
            self.script.append(text)
            return super().send(content, text_format=text_format, label=label)

    episode = make_episode(EpisodeSpec(CONFIGS["cloud_luna_vision"], SLICES["nominal"], 1000, 25.0), None, Opening([]))
    summary = episode.run()
    assert [pick["status"] for pick in summary["picks"]] == ["placed", "placed"]
    assert summary["placed"] == 2 and summary["outcome"] == "planner_done"
    # Each arm said done from beside the bottle, where its own gripper is in the camera's view: it was asked again
    # from its rest pose (outside the window), and that done counted.
    again = [e["arm"] for e in summary["events"] if e["event"] == "done_asked_again_from_rest"]
    assert sorted(again) == ["left", "right"] and summary["vision_planner"]["asked_again_from_rest"] == 2
    nexts = [r["next"] for r in episode.skill_planner.records]
    assert nexts.count("delivered") == 4 and sum(n.startswith("asked_again") for n in nexts) == 2


def test_a_pick_counts_as_placed_only_when_a_pill_it_lifted_ends_in_the_bottle():
    """The other arm may drop a pill into the bottle while this arm's pick is under way: that pill is not this
    pick's. Only when the gripper held something no pill was measured lifted for does a pill entering count."""
    from types import SimpleNamespace

    from convoy_sim.bimanual_pill_task.point_skills import PickAtPoint

    def view(index, in_bottle, lost=False):
        return SimpleNamespace(index=index, in_bottle=in_bottle, lost=lost)

    def outcome(lifted, pills):
        skill = object.__new__(PickAtPoint)  # the retreat's bookkeeping only: no world, arm or grasp needed
        skill.world, skill.arm = SimpleNamespace(pills=lambda: pills), SimpleNamespace(side="left")
        skill.space, skill.t0, skill.phase, skill.result = SimpleNamespace(leave=lambda side: None), 0.0, "retreat", None
        skill.lifted, skill.in_bottle_before = lifted, frozenset({1})
        skill._advance(1.0)
        return skill.result.status

    assert outcome([3], [view(1, True), view(3, False), view(5, True)]) == "missed"  # pill 5 was the other arm's
    assert outcome([3], [view(1, True), view(3, True), view(5, True)]) == "placed"
    assert outcome([3], [view(1, True), view(3, False, lost=True)]) == "lost"
    assert outcome([], [view(1, True), view(5, True)]) == "placed"  # held, nothing measured lifted: pill 5 counts


def test_evaluate_runs_vision_episodes_records_every_call_and_stops_at_the_spend_cap(tmp_path):
    from convoy_sim.bimanual_pill_task.evaluate import evaluate

    with pytest.raises(ValueError, match="no stand-in"):
        evaluate(tmp_path / "none", ["cloud_luna_vision"], ["nominal"], [0])

    class Done(_Scripted):
        def send(self, content, *, text_format=None, label=""):
            me = "L" if "Arm L is free" in content[0]["text"] else "R"
            self.script.append(json.dumps({"arm": me, "action": "done", "u": None, "v": None}))
            return super().send(content, text_format=text_format, label=label)

    report = evaluate(tmp_path / "run", ["cloud_luna_vision"], ["nominal"], [0], horizon=6.0, planner=Done([]))
    (episode,) = report["episodes"]
    assert episode["status"] == "completed" and episode["outcome"] == "planner_done"  # both arms said done
    calls = [json.loads(line) for line in (tmp_path / "run" / "cloud_luna_vision" / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 2 and calls[0]["transport"] == "scripted-test" and "Arm" in calls[0]["instruction"]
    manifest = json.loads((tmp_path / "run" / "manifest.json").read_text())
    assert manifest["vision_planner"]["prompt_version"] == PROMPT_VERSION
    assert "no pill pose is read" in manifest["evidence_scope"]

    class Capped(Done):
        ledger = SpendLedger(tmp_path / "capped.jsonl", cap_usd=0.0)

        def worst_case_usd(self):
            return 0.001

    capped = evaluate(tmp_path / "capped", ["cloud_luna_vision"], ["nominal"], [0, 1], horizon=6.0, planner=Capped([]))
    assert [e["status"] for e in capped["episodes"]] == ["not_run", "not_run"]
    assert capped["episodes"][0]["reason"].startswith("spend cap")
