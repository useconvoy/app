"""Owner-scoped offline evaluations over authenticated HTTP: imports, replay shape, limits and storage."""

from __future__ import annotations

import base64
import json
import random
import sqlite3
import struct
import zlib
from datetime import timedelta

import pytest
from conftest import WEB, api_token_client, login, make_user
from convoy_server import migrations
from convoy_server.chat_body_limit import OFFLINE_EPISODE_BODY
from convoy_server.db import make_engine, reset_engine, session_scope, write_txn
from convoy_server.models import Base, Throttle
from convoy_server.offline_models import OfflineEpisode, OfflineEvaluation
from convoy_server.platform_models import MutationReceipt
from convoy_server.services import offline_evaluations as service
from convoy_server.services import offline_recordings as recordings
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_platform_lifecycle import pipeline  # noqa: F401

ROOT = "/api/v1/offline-evaluations"
LABELS = {"name": "Bimanual · nominal", "task": "Pills into bottle", "config_label": "Edge planner r1",
          "policy_label": "Scripted v2"}
# A 16 × 12 baseline JPEG written by Pillow 12.3.0 (quality 80).
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAYEBQYFBAYGBQYHBwYIChAKCgkJChQODwwQFxQYGBcUFhYaHSUfGhsjHBYWICwgIyYnKSop"
    "GR8tMC0oMCUoKSj/2wBDAQcHBwoIChMKChMoGhYaKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgo"
    "KCj/wAARCAAMABADASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQID"
    "AAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlq"
    "c3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP09fb3"
    "+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEI"
    "FEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImK"
    "kpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDz"
    "TS/CnT93+ldfpXhTp+7/AErutL0624+Suw0vTrbj5KMbndTU4+GOIqvun//Z"
)
REPLAY_KEYS = {"episode_id", "mission_id", "release_digest", "steps", "skill", "planner_ms", "wall_seconds",
               "sim_seconds", "source"}
FRAME_KEYS = {"index", "image_png_base64", "action", "reward", "success", "policy_ms"}


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(width=32, height=24, shade=0, *, interlace=0, depth=8, color=2, extra=b"", data=None) -> bytes:
    """A real RGB8 PNG of generic pixels (or the given scanlines), written with the standard library."""
    channels = {0: 1, 2: 3, 6: 4}[color]
    row = bytes((shade + 7 * x) % 256 for x in range(width * channels * depth // 8))
    scanlines = data if data is not None else b"".join(b"\0" + row for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, depth, color, 0, 0, interlace)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + extra + chunk(b"IDAT", zlib.compress(scanlines)) + chunk(b"IEND", b"")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def episode(steps=3, width=14, *, seed=0, outcome="success", images=None, **fields) -> dict:
    """An upload in the replay format: frame 0 is the start, frame k follows action k."""
    images = images if images is not None else {index: png(shade=index) for index in range(steps + 1)}
    frames = [{"index": 0, "image_png_base64": b64(images[0])}]
    for index in range(1, steps + 1):
        frames.append({
            "index": index, "image_png_base64": b64(images[index]) if index in images else None,
            "action": [round(0.1 * index - 0.01 * axis, 4) for axis in range(width)],
            "reward": 0.5 * index, "success": index == steps and outcome == "success", "policy_ms": 10.0 + index,
        })
    body = {"seed": seed, "outcome": outcome, "metrics": {"reward_sum": 3.0, "grasped": True, "note": "generic"},
            "action_labels": [f"j{axis}" for axis in range(width)], "wall_seconds": 2.5 + seed, "sim_seconds": 0.0375,
            "frames": frames}
    body.update(fields)
    return body


def create(client, key="create", headers=WEB, **fields):
    return client.post(ROOT, json={**LABELS, **fields}, headers={**headers, "Idempotency-Key": key})


def upload(client, evaluation_id, body, key, headers=WEB):
    return client.post(f"{ROOT}/{evaluation_id}/episodes", json=body, headers={**headers, "Idempotency-Key": key})


def upload_raw(client, evaluation_id, content, key, **headers):
    sent = {**WEB, "Content-Type": "application/json", "Idempotency-Key": key, **headers}
    return client.post(f"{ROOT}/{evaluation_id}/episodes", content=content, headers={k: v for k, v in sent.items() if v is not None})


def remove(client, path, key, headers=WEB):
    return client.delete(f"{ROOT}/{path}", headers={**headers, "Idempotency-Key": key})


def user_client(app, admin, email, role):
    make_user(admin, email, role)
    return login(TestClient(app), email, "password-123")


def store(settings):
    return settings.data_dir / "recordings"


@pytest.fixture()
def evaluation(admin):
    created = create(admin)
    assert created.status_code == 201, created.text
    return created.json()


def test_evaluations_are_owner_scoped_unsigned_and_need_the_operator_role(app, admin):
    viewer = user_client(app, admin, "viewer@example.com", "viewer")
    operator = user_client(app, admin, "operator@example.com", "operator")
    refused = create(viewer)
    assert refused.status_code == 403 and refused.json() == {"error": "requires role operator"}
    assert viewer.get(ROOT).json() == {"items": []}

    created = create(operator)
    assert created.status_code == 201, created.text
    data = created.json()
    assert list(data) == ["id", "name", "task", "config_label", "policy_label", "summary", "source", "signed",
                          "scope", "created_at", "updated_at"]
    assert data["id"].startswith("oev_") and {k: data[k] for k in LABELS} == LABELS
    assert (data["source"], data["signed"]) == ("offline", False) and "not run, verified or signed by Convoy" in data["scope"]
    assert data["summary"] == {"episodes": 0, "successes": 0, "success_rate": None, "median_steps": None,
                               "median_wall_seconds": None, "median_sim_seconds": None, "stored_bytes": 0}
    assert operator.get(ROOT).json() == {"items": [data]}
    assert operator.get(f"{ROOT}/{data['id']}").json() == {**data, "episodes": []}

    # Nobody else sees it, administrators included.
    oid = data["id"]
    for other in (admin, viewer):
        assert other.get(ROOT).json() == {"items": []}
        assert other.get(f"{ROOT}/{oid}").status_code == 404
    missing = upload(admin, oid, episode(), "not-yours")
    assert missing.status_code == 404 and missing.json() == {"error": "offline evaluation not found"}
    assert remove(admin, oid, "not-yours").status_code == 404

    # An API token acts as its user without the browser header; a browser session needs it to write.
    token = api_token_client(app, operator, name="importer")
    stored = upload(token, oid, episode(), "token-upload", headers={})
    assert stored.status_code == 201, stored.text
    for response in (
        operator.post(ROOT, json=LABELS, headers={"Idempotency-Key": "no-header"}),
        operator.post(f"{ROOT}/{oid}/episodes", json=episode(), headers={"Idempotency-Key": "no-header"}),
        operator.delete(f"{ROOT}/{oid}", headers={"Idempotency-Key": "no-header"}),
    ):
        assert response.status_code == 403 and response.json() == {"error": "missing X-Convoy-Client header"}

    anonymous = TestClient(app)
    episode_path = f"{oid}/episodes/{stored.json()['id']}"
    for response in (
        anonymous.get(ROOT), anonymous.get(f"{ROOT}/{oid}"), create(anonymous), upload(anonymous, oid, episode(), "x"),
        anonymous.get(f"{ROOT}/{episode_path}/replay"), anonymous.get(f"{ROOT}/{episode_path}/replay/frames/0"),
        remove(anonymous, oid, "x"), remove(anonymous, episode_path, "x"),
    ):
        assert response.status_code == 401 and response.json() == {"error": "authentication required"}
    for other in (admin, viewer):
        assert other.get(f"{ROOT}/{episode_path}/replay").status_code == 404
        assert other.get(f"{ROOT}/{episode_path}/replay/frames/0").status_code == 404

    # A demoted owner still reads their evaluations but can no longer write.
    with session_scope() as session, write_txn(session):
        from convoy_server.models import User

        session.scalar(select(User).where(User.email == "operator@example.com")).role = "viewer"
    assert token.get(f"{ROOT}/{oid}").status_code == 200
    assert upload(token, oid, episode(seed=9), "demoted", headers={}).status_code == 403


def test_an_episode_replays_in_the_hosted_replay_shape(admin, evaluation, settings):
    images = {0: png(shade=1), 1: JPEG, 3: png(16, 16, shade=3)}  # frame 2 replays frame 1's image
    body = episode(steps=3, width=14, images=images, seed=7, skill="put_pills", planner_ms=812.5)
    stored = upload(admin, evaluation["id"], body, "upload")
    assert stored.status_code == 201, stored.text
    data = stored.json()
    assert list(data) == ["id", "evaluation_id", "seed", "outcome", "steps", "images", "action_dim", "metrics",
                          "wall_seconds", "sim_seconds", "stored_bytes", "created_at"]
    assert data["id"].startswith("oep_") and data["evaluation_id"] == evaluation["id"]
    assert (data["seed"], data["outcome"], data["steps"], data["images"], data["action_dim"]) == (7, "success", 3, 3, 14)
    assert data["metrics"] == {"reward_sum": 3.0, "grasped": True, "note": "generic"}
    path = store(settings) / f"{data['id']}.sqlite3"
    assert path.is_file() and data["stored_bytes"] == path.stat().st_size
    assert not list(store(settings).glob("*.partial"))

    read = admin.get(f"{ROOT}/{evaluation['id']}").json()
    assert read["episodes"] == [data]
    assert read["summary"] == {"episodes": 1, "successes": 1, "success_rate": 1.0, "median_steps": 3,
                               "median_wall_seconds": 9.5, "median_sim_seconds": 0.0375, "stored_bytes": data["stored_bytes"]}

    base = f"{ROOT}/{evaluation['id']}/episodes/{data['id']}/replay"
    manifest = admin.get(base).json()
    assert REPLAY_KEYS <= set(manifest)
    assert manifest == {
        "episode_id": data["id"], "mission_id": None, "release_digest": None, "steps": 3, "skill": "put_pills",
        "planner_ms": 812.5, "wall_seconds": 9.5, "sim_seconds": 0.0375, "source": service.REPLAY_SOURCE,
        "evaluation_id": evaluation["id"], "action_labels": [f"j{axis}" for axis in range(14)], "action_dim": 14,
        "images": 3,
    }
    frames = [admin.get(f"{base}/frames/{index}").json() for index in range(4)]
    assert all(FRAME_KEYS <= set(frame) for frame in frames)
    assert frames[0] == {"index": 0, "image_png_base64": b64(images[0]), "action": None, "reward": None,
                         "success": None, "policy_ms": None, "image_media_type": "image/png", "image_index": 0}
    assert frames[1]["image_png_base64"] == b64(JPEG) and frames[1]["image_media_type"] == "image/jpeg"
    assert frames[1]["action"] == body["frames"][1]["action"] and len(frames[1]["action"]) == 14
    assert (frames[1]["reward"], frames[1]["success"], frames[1]["policy_ms"]) == (0.5, False, 11.0)
    assert frames[2]["image_png_base64"] == b64(JPEG) and frames[2]["image_index"] == 1
    assert frames[2]["action"] == body["frames"][2]["action"]
    assert frames[3]["image_index"] == 3 and frames[3]["success"] is True
    for index in (4, 2049):
        assert admin.get(f"{base}/frames/{index}").status_code in (404, 422)
    assert admin.get(f"{base}/frames/4").json() == {"error": "Recording is not available for this episode"}
    # A recording that does not match its row is not served.
    with sqlite3.connect(path) as db:
        db.execute("UPDATE episode SET digest = 'f'")
    assert admin.get(base).status_code == 404
    assert admin.get(f"{base}/frames/0").status_code == 404


def test_episode_uploads_are_validated_whole(admin, evaluation, monkeypatch):
    oid = evaluation["id"]

    def refused(body, fragment, status=422):
        response = upload(admin, oid, body, "invalid")
        assert response.status_code == status, (fragment, response.text)
        assert fragment in response.json()["error"], response.json()

    good = episode()
    refused({**good, "frames": good["frames"][:1]}, "frames: List should have at least 2 items")
    refused({**good, "frames": [good["frames"][0], {**good["frames"][2], "index": 2}]}, "frames.1.index: expected 1")
    refused({**good, "frames": [{**good["frames"][0], "action": [0.0] * 14}, *good["frames"][1:]]}, "frames.0: frame 0 precedes")
    refused({**good, "frames": [{**good["frames"][0], "image_png_base64": None}, *good["frames"][1:]]}, "frames.0.image_png_base64")
    refused({**good, "frames": [*good["frames"][:2], {**good["frames"][2], "action": None}, good["frames"][3]]}, "frames.2.action: every step")
    refused({**good, "frames": [*good["frames"][:2], {**good["frames"][2], "action": [0.0] * 13}, good["frames"][3]]}, "expected 14 values")
    refused({**good, "action_labels": ["x"] * 13}, "action_labels: expected 14 labels")
    refused({**good, "frames": [*good["frames"][:1], {**good["frames"][1], "action": [2e6] * 14}, *good["frames"][2:]]}, "frames.1.action.0")
    refused({**good, "frames": [*good["frames"][:1], {**good["frames"][1], "action": []}, *good["frames"][2:]]}, "frames.1.action")
    refused({**good, "frames": [*good["frames"][:1], {**good["frames"][1], "image_png_base64": "not base64!"}, *good["frames"][2:]]}, "invalid base64")
    refused({**good, "frames": [*good["frames"][:1], {**good["frames"][1], "image_png_base64": b64(b"GIF89a....")}, *good["frames"][2:]]}, "PNG or a JPEG")
    refused({**good, "frames": [{**good["frames"][0], "image_png_base64": b64(png(321, 10))}, *good["frames"][1:]]}, "each side must be 1–320 pixels")
    refused({**good, "frames": [*good["frames"][:1], {**good["frames"][1], "extra": 1}, *good["frames"][2:]]}, "frames.1.extra")
    refused({**good, "outcome": "passed"}, "outcome")
    refused({**good, "seed": -1}, "seed")
    refused({**good, "seed": 1.5}, "seed")
    refused({**good, "metrics": {"Bad-Key": 1}}, "metrics")
    refused({**good, "metrics": {f"m{index}": index for index in range(33)}}, "metrics")
    refused({**good, "metrics": {"text": "x" * 201}}, "metrics.text")
    refused({**good, "metrics": {f"m{index}": "x" * 150 for index in range(30)}}, "metrics exceed 4096 bytes")
    refused({**good, "wall_seconds": -1}, "wall_seconds")
    refused({**good, "owner_user_id": "someone"}, "owner_user_id")
    refused([good], "Input should be an object")
    for content in (b'{"seed":', b"[" * 5000 + b"]" * 5000, b'{"seed": 1}' + bytes([255])):
        response = upload_raw(admin, oid, content, "raw")
        assert response.status_code == 422 and response.json()["detail"][0]["type"] == "json_invalid", response.text
    for token in ("NaN", "Infinity", "-Infinity"):
        content = json.dumps({**good, "wall_seconds": 1}).replace('"wall_seconds": 1', f'"wall_seconds": {token}').encode()
        response = upload_raw(admin, oid, content, token)
        assert response.status_code == 422 and "wall_seconds: Input should be a finite number" in response.json()["error"]
    text = upload_raw(admin, oid, json.dumps(good).encode(), "text", **{"Content-Type": "text/plain"})
    assert text.status_code == 422 and text.json() == {"error": "the request body must be JSON (Content-Type: application/json)"}
    monkeypatch.setattr(service, "MAX_IMAGES", 2)
    refused(good, "at most 2 frames may carry an image")
    monkeypatch.undo()
    # A sparse episode needs only frame 0's image; the action width is anything from 1 to 64.
    assert upload(admin, oid, episode(steps=5, width=1, images={0: png()}), "sparse").status_code == 201
    assert upload(admin, oid, episode(steps=1, width=64, images={0: png(), 1: JPEG}, action_labels=None), "wide").status_code == 201
    refused(episode(steps=1, width=65), "frames.1.action")
    assert admin.get(f"{ROOT}/{oid}").json()["summary"]["episodes"] == 2
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(OfflineEpisode)) == 2


def test_images_are_checked_without_an_image_library():
    for good in (png(), png(320, 320), png(8, 8, color=0), png(8, 8, color=6), png(8, 8, depth=16, color=2), JPEG,
                 png(extra=chunk(b"tEXt", b"Comment\0generic"))):
        recordings.image(good)
    for bad, reason in (
        (png()[:-5], "PNG"),
        (png().replace(b"IEND", b"IEN\0", 1), "PNG"),
        (png() + b"trailing", "invalid PNG end"),
        (png(interlace=1), "non-interlaced"),
        (png(8, 8, depth=4, color=2), "colour type or bit depth"),
        (png(extra=chunk(b"CgBI", b"\x50\x00\x20\x02")), "unsupported PNG chunk CgBI"),  # Apple's critical chunk
        (png(8, 8, color=0).replace(struct.pack(">IIBBBBB", 8, 8, 8, 0, 0, 0, 0), struct.pack(">IIBBBBB", 8, 8, 8, 3, 0, 0, 0)), "PNG"),
        (png(8, 8, data=b"\0" * (9 * 25)), "does not match its header"),  # more rows than declared
        (png(8, 8, data=b"\0" * (7 * 25)), "does not match its header"),  # fewer rows than declared
        (png(8, 8, data=b"\0" * 10_000_000), "does not match its header"),  # a decompression bomb stops early
        (png(8, 8, data=b"".join(b"\x07" + b"\0" * 24 for _ in range(8))), "does not match its header"),  # bad filter
        (png(0, 8), "each side must be 1–320 pixels"),
        (JPEG[:-2], "EOI"),
        (JPEG.replace(b"\xff\xc0", b"\xff\xc9", 1), "baseline or progressive"),
        (JPEG.replace(b"\xff\xc0\x00\x11\x08\x00\x0c\x00\x10", b"\xff\xc0\x00\x11\x08\x01\x50\x00\x10", 1), "1–320 pixels"),
        (b"\xff\xd8\xff\xda\x00\x02\xff\xd9", "precedes its frame header"),
        (b"\xff\xd8" + b"\0" * 10 + b"\xff\xd9", "invalid JPEG marker"),
        (b"\xff\xd8\xff\xe0\x00\x80\xff\xd9", "truncated JPEG segment"),
        (b"GIF89a", "PNG or a JPEG"),
        (png() + b"\0" * recordings.MAX_IMAGE_BYTES, "exceeds"),
    ):
        with pytest.raises(ValueError, match=reason):
            recordings.image(bad)


def test_uploads_are_idempotent_deduplicated_audited_and_keep_compact_receipts(admin, evaluation):
    oid = evaluation["id"]
    body = episode(seed=1)
    first = upload(admin, oid, body, "upload-1")
    assert first.status_code == 201
    replay = upload(admin, oid, body, "upload-1")
    assert replay.status_code == 201 and replay.json() == first.json()
    # Identical content under another key is the same episode, not a second one.
    duplicate = upload(admin, oid, body, "upload-2")
    assert duplicate.status_code == 200 and duplicate.json() == first.json()
    reused = upload(admin, oid, episode(seed=2), "upload-1")
    assert reused.status_code == 409 and reused.json() == {"error": "Idempotency-Key was already used with a different payload"}
    second = upload(admin, oid, episode(seed=2, outcome="failure"), "upload-3")
    assert second.status_code == 201 and second.json()["id"] != first.json()["id"]
    summary = admin.get(f"{ROOT}/{oid}").json()["summary"]
    assert (summary["episodes"], summary["successes"], summary["success_rate"], summary["median_wall_seconds"]) == (2, 1, 0.5, 4.0)
    assert create(admin).json() == evaluation  # the evaluation's own key replays too
    assert create(admin, name="Other").status_code == 409

    with session_scope() as session:
        receipts = session.scalars(select(MutationReceipt).where(MutationReceipt.route.like(f"{ROOT}%"))).all()
        assert len(receipts) == 4
        assert all(len(json.dumps(receipt.response)) < 1024 for receipt in receipts)  # metadata, never frames
    entries = [e for e in admin.get("/api/v1/audit").json() if e["action"].startswith("offline_evaluation.")]
    entries.reverse()
    assert [e["action"] for e in entries] == ["offline_evaluation.create", "offline_evaluation.episode", "offline_evaluation.episode"]
    assert entries[0]["details"] == LABELS and entries[0]["target"] == oid
    assert set(entries[1]["details"]) == {"episode_id", "seed", "outcome", "steps", "images", "stored_bytes"}
    assert all("frames" not in json.dumps(e["details"]) and "base64" not in json.dumps(e["details"]) for e in entries)

    # Deletion: an episode, then the evaluation; each with its own key, replayed on retry.
    episode_id = second.json()["id"]
    assert remove(admin, f"{oid}/episodes/{episode_id}", "delete-episode").status_code == 204
    assert remove(admin, f"{oid}/episodes/{episode_id}", "delete-episode").status_code == 204
    assert remove(admin, f"{oid}/episodes/{episode_id}", "delete-episode-again").status_code == 404
    assert admin.get(f"{ROOT}/{oid}").json()["summary"]["episodes"] == 1
    assert remove(admin, oid, "delete").status_code == 204
    assert remove(admin, oid, "delete").status_code == 204
    assert remove(admin, oid, "delete-again").json() == {"error": "offline evaluation not found"}
    assert admin.get(ROOT).json() == {"items": []}
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(OfflineEpisode)) == 0
        assert session.scalar(select(func.count()).select_from(OfflineEvaluation)) == 0
    actions = [e["action"] for e in admin.get("/api/v1/audit").json() if e["action"].startswith("offline_evaluation.")]
    assert actions[:2] == ["offline_evaluation.delete", "offline_evaluation.episode_delete"]
    for key in ("", "x" * 129, "has space"):
        assert create(admin, key=key).status_code == 422
        assert remove(admin, oid, key).status_code == 422
    assert admin.post(ROOT, json=LABELS, headers=WEB).status_code == 422  # no Idempotency-Key


def test_frames_share_the_recording_store_and_its_quota(app, admin, evaluation, settings, monkeypatch):
    oid = evaluation["id"]
    root = store(settings)
    first = upload(admin, oid, episode(seed=1), "first").json()
    size = first["stored_bytes"]
    # A hosted recording in the same store counts against offline uploads, and the other way round.
    (root / "epi_hostedrecord.sqlite3").write_bytes(b"\0" * 200_000)
    used = sum(path.stat().st_size for path in root.iterdir() if path.is_file())
    monkeypatch.setenv("CONVOY_RECORDING_QUOTA_BYTES", str(used + 30_000))
    full = upload(admin, oid, episode(seed=2), "shared-full")
    assert full.status_code == 507 and full.json()["error"].startswith("Recording storage limit reached")
    monkeypatch.setenv("CONVOY_RECORDING_QUOTA_BYTES", str(used + size + 200_000))
    # The account quota fits one more episode of this size, but this account already holds one.
    estimate = service.decode_episode(json.dumps(episode(seed=2)).encode()).estimate
    monkeypatch.setenv("CONVOY_OFFLINE_EVALUATION_QUOTA_BYTES", str(recordings.MARGIN + estimate + size // 2))
    mine = upload(admin, oid, episode(seed=2), "account-full")
    assert mine.status_code == 507 and mine.json()["error"].startswith("Offline evaluation storage limit reached")
    assert sorted(path.name for path in root.iterdir() if path.name.startswith("oep_")) == [f"{first['id']}.sqlite3"]
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(OfflineEpisode)) == 1
    # Each account has its own offline quota.
    second = user_client(app, admin, "second@example.com", "operator")
    other = upload(second, create(second, key="second-create").json()["id"], episode(seed=2), "other-account")
    assert other.status_code == 201, other.text
    monkeypatch.delenv("CONVOY_OFFLINE_EVALUATION_QUOTA_BYTES")
    assert upload(admin, oid, episode(seed=2), "fits").status_code == 201
    # Deleting the evaluation frees its files.
    assert remove(admin, oid, "delete").status_code == 204
    assert sorted(path.name for path in root.iterdir() if path.name.startswith("oep_")) == [f"{other.json()['id']}.sqlite3"]


def test_offline_files_count_against_hosted_recording_uploads(pipeline, settings, tmp_path, monkeypatch):  # noqa: F811
    """The existing quota mechanism, unchanged: a hosted upload sees offline files in the same store."""
    from test_recording_upload import completed

    admin = pipeline["admin"]
    oid = create(admin, key="offline").json()["id"]
    assert upload(admin, oid, episode(), "offline").status_code == 201
    hosted, payload = completed(pipeline, tmp_path, monkeypatch)
    used = sum(path.stat().st_size for path in store(settings).iterdir() if path.is_file())
    # Room for the hosted command's own allowance (64 KiB) plus a little, not for the offline file as well.
    monkeypatch.setenv("CONVOY_RECORDING_QUOTA_BYTES", str(used + 72 * 1024))
    route = f"{pipeline['base']}/missions/{hosted.mission_id}/recording/commands/0"
    full = pipeline["agent"].client.post(route, json=payload)
    assert full.status_code == 507 and full.json() == {"error": "Recording storage limit reached"}
    assert remove(admin, oid, "free").status_code == 204  # offline files can be deleted; hosted ones cannot
    assert pipeline["agent"].client.post(route, json=payload).status_code == 200


def test_interrupted_writes_are_settled_by_the_next_write(admin, evaluation, settings):
    oid = evaluation["id"]
    root = store(settings)
    first = upload(admin, oid, episode(seed=1), "first").json()
    second = upload(admin, oid, episode(seed=2), "second").json()
    replay = f"{ROOT}/{oid}/episodes/{first['id']}/replay"
    # Committed but not yet renamed: readable as it is, and published by the next write.
    (root / f"{first['id']}.sqlite3").rename(root / f"{first['id']}.partial")
    assert admin.get(replay).status_code == 200
    # Removal that did not commit: the row still exists, so the recording comes back.
    (root / f"{second['id']}.sqlite3").rename(root / f"{second['id']}.removed")
    assert admin.get(f"{ROOT}/{oid}/episodes/{second['id']}/replay").status_code == 404
    # Never committed, or removed: nothing refers to these files.
    (root / "oep_abandoned001.partial").write_bytes(b"partial upload")
    (root / "oep_abandoned002.removed").write_bytes(b"removed recording")
    (root / "epi_hostedpartial.partial").write_bytes(b"a hosted upload in progress")
    assert upload(admin, oid, episode(seed=3), "third").status_code == 201
    names = sorted(path.name for path in root.iterdir() if path.is_file() and not path.name.startswith("."))
    third = admin.get(f"{ROOT}/{oid}").json()["episodes"][2]["id"]
    assert names == sorted(["epi_hostedpartial.partial", *(f"{e}.sqlite3" for e in (first["id"], second["id"], third))])
    assert admin.get(f"{ROOT}/{oid}/episodes/{second['id']}/replay").status_code == 200


def test_body_is_read_only_after_authentication_ownership_and_admission(app, admin, evaluation, monkeypatch):
    decoded: list[int] = []
    original = service.decode_episode
    monkeypatch.setattr(service, "decode_episode", lambda raw: decoded.append(len(raw)) or original(raw))
    payload = json.dumps(episode()).encode()
    consumed: list[int] = []

    def body():
        consumed.append(len(payload))
        yield payload

    def attempt(client, evaluation_id=evaluation["id"], **headers):
        merged = {**WEB, "Content-Type": "application/json", "Idempotency-Key": "upload", **headers}
        sent = {key: value for key, value in merged.items() if value is not None}
        return client.post(f"{ROOT}/{evaluation_id}/episodes", content=body(), headers=sent)

    viewer = user_client(app, admin, "uploader@example.com", "viewer")
    operator = user_client(app, admin, "stranger@example.com", "operator")
    stranger = TestClient(app)
    stranger.headers.update({"Authorization": "Bearer cva_not-a-token"})
    refusals = [
        (attempt(TestClient(app)), 401),
        (attempt(stranger), 401),
        (attempt(viewer), 403),
        (attempt(admin, **{"X-Convoy-Client": None}), 403),
        (attempt(admin, evaluation_id="not-an-id"), 422),
        (attempt(operator), 404),  # someone else's evaluation
        (attempt(admin, evaluation_id="oev_000000000000"), 404),
        (attempt(admin, **{"Idempotency-Key": None}), 422),
        (attempt(admin, **{"Content-Type": "text/plain"}), 422),
    ]
    assert [response.status_code for response, _ in refusals] == [status for _, status in refusals]
    declared = TestClient(app).post(f"{ROOT}/{evaluation['id']}/episodes", content=payload, headers={**WEB, "Content-Type": "application/json"})
    assert declared.status_code == 401
    assert consumed == [] and decoded == []

    assert attempt(admin).status_code == 201
    assert consumed == [len(payload)] and decoded == [len(payload)]
    monkeypatch.setattr(service, "WRITE_LIMIT", 1)
    throttled = attempt(admin, **{"Idempotency-Key": "again"})
    assert throttled.status_code == 429
    assert consumed == [len(payload)] and decoded == [len(payload)]


def test_request_bodies_are_bounded_before_decoding(admin, evaluation):
    oid = evaluation["id"]
    for content in (b"x" * (OFFLINE_EPISODE_BODY + 1), iter([b"x" * (OFFLINE_EPISODE_BODY + 1)])):
        response = upload_raw(admin, oid, content, "oversized")
        assert response.status_code == 413 and "too large" in response.json()["error"]
    # Creation keeps the ordinary management bound; the larger allowance is for episodes only.
    assert admin.post(ROOT, content=b"x" * (256 * 1024 + 1), headers={**WEB, "Content-Type": "application/json", "Idempotency-Key": "big"}).status_code == 413
    assert admin.post(f"{ROOT}/{oid}/other", content=b"x" * (256 * 1024 + 1), headers=WEB).status_code == 413
    # A large valid episode, sent in pieces: 41 incompressible 320 × 240 frames, about 12 MiB as base64.
    noise = {index: png(320, 240, data=b"".join(b"\0" + random.Random(index * 1000 + y).randbytes(960) for y in range(240)))
             for index in range(41)}
    assert all(200_000 < len(image) <= recordings.MAX_IMAGE_BYTES for image in noise.values())
    content = json.dumps(episode(steps=40, width=7, images=noise)).encode()
    assert 10 * 1024 * 1024 < len(content) < OFFLINE_EPISODE_BODY
    stored = upload_raw(admin, oid, iter([content[:5_000_000], content[5_000_000:]]), "large")
    assert stored.status_code == 201 and stored.json()["images"] == 41
    frame = admin.get(f"{ROOT}/{oid}/episodes/{stored.json()['id']}/replay/frames/40").json()
    assert base64.b64decode(frame["image_png_base64"]) == noise[40]


def test_writes_are_rate_limited_per_account(app, admin, evaluation, monkeypatch):
    monkeypatch.setattr(service, "WRITE_LIMIT", 2)
    oid = evaluation["id"]  # the creation counted once
    assert upload(admin, oid, episode(seed=1), "one").status_code == 201
    for limited in (upload(admin, oid, episode(seed=2), "two"), create(admin, key="again", name="Again"), remove(admin, oid, "delete")):
        assert limited.status_code == 429
        assert limited.json() == {"error": "too many offline evaluation writes (at most 2 per 10 minutes)"}
        assert 0 < int(limited.headers["Retry-After"]) <= service.WRITE_WINDOW_S
    operator = user_client(app, admin, "own-budget@example.com", "operator")
    assert create(operator).status_code == 201  # every account has its own budget
    assert admin.get(f"{ROOT}/{oid}").status_code == 200  # reads are not limited
    with session_scope() as session, write_txn(session):
        throttle = session.get(Throttle, f"offline-evaluations:{admin.get('/api/v1/auth/me').json()['user']['id']}")
        throttle.window_start -= timedelta(seconds=service.WRITE_WINDOW_S + 1)
    assert upload(admin, oid, episode(seed=2), "two").status_code == 201


def test_counts_are_bounded(admin, evaluation, monkeypatch):
    monkeypatch.setattr(service, "MAX_EPISODES", 1)
    assert upload(admin, evaluation["id"], episode(seed=1), "one").status_code == 201
    full = upload(admin, evaluation["id"], episode(seed=2), "two")
    assert full.status_code == 409 and full.json() == {"error": "offline episode limit reached (1 per evaluation)"}
    monkeypatch.setattr(service, "MAX_EVALUATIONS", 2)
    assert create(admin, key="second", name="Second").status_code == 201
    over = create(admin, key="third", name="Third")
    assert over.status_code == 409 and "limit reached (2 per account)" in over.json()["error"]
    assert [item["name"] for item in admin.get(ROOT).json()["items"]] == ["Second", LABELS["name"]]


def test_existing_v5_database_gains_offline_tables_at_startup(admin, evaluation, settings):
    """ADDITIVE under schema version 5, like workspace documents: startup completes an older database."""
    from convoy_server.app import create_app

    reset_engine()
    connection = sqlite3.connect(settings.data_dir / "convoy.db")
    connection.execute("DROP TABLE offline_episodes")
    connection.execute("DROP TABLE offline_evaluations")
    connection.commit()
    connection.close()
    engine = make_engine(settings)
    try:
        plan = migrations.plan_migration(engine)
        assert sorted(plan["add_tables"]) == ["offline_episodes", "offline_evaluations"] and not plan["needs_rebuild"]
        applied = migrations.ensure_schema(engine)
        assert sorted(applied["add_tables"]) == ["offline_episodes", "offline_evaluations"]
        assert migrations.user_version(engine) == migrations.SCHEMA_VERSION == 5
    finally:
        engine.dispose()
    connection = sqlite3.connect(settings.data_dir / "convoy.db")
    unique = [
        [column[2] for column in connection.execute(f'PRAGMA index_info("{name}")')]
        for _, name, flag, *_ in connection.execute("PRAGMA index_list(offline_episodes)") if flag
    ]
    assert ["evaluation_id", "digest"] in unique
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    with TestClient(create_app(settings, start_scheduler=False)) as restarted:
        login(restarted)
        assert restarted.get(ROOT).json() == {"items": []}
        created = create(restarted, key="after-upgrade")
        assert upload(restarted, created.json()["id"], episode(), "after-upgrade").status_code == 201


def test_previous_release_starts_on_a_database_with_offline_evaluations(admin, evaluation, settings, monkeypatch):
    """Rollback keeps the data directory: the release before offline evaluations (schema 5 without these
    tables) must accept the database unchanged, and its recording store keeps working beside the files."""
    assert upload(admin, evaluation["id"], episode(), "before-rollback").status_code == 201
    reset_engine()
    previous = {name: table for name, table in Base.metadata.tables.items() if not name.startswith("offline_")}
    monkeypatch.setattr(migrations, "_model_tables", lambda: previous)
    engine = make_engine(settings)
    try:
        assert migrations.ensure_schema(engine) == {"user_version": 5, "unchanged": True}
    finally:
        engine.dispose()
    connection = sqlite3.connect(settings.data_dir / "convoy.db")
    assert connection.execute("SELECT count(*) FROM offline_episodes").fetchone() == (1,)
    connection.close()
