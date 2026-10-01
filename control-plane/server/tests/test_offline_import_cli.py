"""The generic offline-evaluation importer against the real API over HTTP (an API token, SQLite)."""

from __future__ import annotations

import base64
import importlib.util
import json
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from conftest import WEB, login
from fastapi.testclient import TestClient

SCRIPT = Path(__file__).resolve().parents[3] / "integrations" / "simulation" / "scripts" / "import_offline_eval.py"


def load_importer():
    spec = importlib.util.spec_from_file_location("import_offline_eval", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def served(settings):
    from convoy_server.app import create_app

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(settings, start_scheduler=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    yield app, f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)


def test_the_importer_uploads_a_replay_directory_once_and_never_prints_credentials(served, tmp_path, monkeypatch, capsys):
    app, server = served
    importer = load_importer()
    admin = login(TestClient(app))
    token = admin.post("/api/v1/tokens", json={"name": "importer"}, headers=WEB).json()["token"]
    episodes = tmp_path / "episodes"
    importer.write_example(episodes, episodes=2, steps=6)
    # One frame as an image file beside its JSON, one frame that repeats the previous image.
    first = episodes / "episode-000" / "frames"
    frame = json.loads((first / "0003.json").read_text())
    (first / "0003.png").write_bytes(base64.b64decode(frame.pop("image_png_base64")))
    (first / "0003.json").write_text(json.dumps(frame))
    frame = json.loads((first / "0004.json").read_text())
    (first / "0004.json").write_text(json.dumps({**frame, "image_png_base64": None}))

    for name in ("CONVOY_EMAIL", "CONVOY_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert importer.main([str(episodes), "--dry-run"]) == 0  # no server, no credentials
    assert "nothing uploaded" in capsys.readouterr().out
    monkeypatch.setenv("CONVOY_SERVER", server)
    monkeypatch.setenv("CONVOY_API_TOKEN", token)
    assert importer.main([str(episodes)]) == 0
    first_run = capsys.readouterr()
    evaluations = admin.get("/api/v1/offline-evaluations").json()["items"]
    assert len(evaluations) == 1 and evaluations[0]["name"] == "Example · generic frames"
    detail = admin.get(f"/api/v1/offline-evaluations/{evaluations[0]['id']}").json()
    assert [(e["seed"], e["outcome"], e["steps"], e["action_dim"]) for e in detail["episodes"]] == [(0, "success", 6, 14), (1, "timeout", 6, 14)]
    assert detail["episodes"][0]["images"] == 6 and detail["episodes"][1]["images"] == 7
    base = f"/api/v1/offline-evaluations/{detail['id']}/episodes/{detail['episodes'][0]['id']}/replay"
    assert admin.get(base).json()["action_labels"][:2] == ["l_x", "l_y"]
    assert admin.get(f"{base}/frames/3").json()["image_png_base64"] == frame_image(episodes, 3)
    assert admin.get(f"{base}/frames/4").json()["image_index"] == 3
    assert first_run.out.count(": stored as oep_") == 2

    # A rerun replays its own writes: the same evaluation, nothing stored twice.
    assert importer.main([str(episodes)]) == 0
    second_run = capsys.readouterr()
    assert second_run.out.count(": stored as oep_") == 2  # receipts replay the original answers
    assert len(admin.get("/api/v1/offline-evaluations").json()["items"]) == 1
    # Another import of the same content into that evaluation finds it stored.
    assert importer.main([str(episodes), "--evaluation", detail["id"], "--name", "Renamed"]) == 0
    assert capsys.readouterr().out.count("already stored") == 2
    assert admin.get(f"/api/v1/offline-evaluations/{detail['id']}").json()["summary"]["episodes"] == 2
    for output in (first_run, second_run):
        assert token not in output.out + output.err

    # Refusals name the reason, never the credential.
    monkeypatch.setenv("CONVOY_API_TOKEN", token[:-4] + "xxxx")
    assert importer.main([str(episodes)]) == 1
    refused = capsys.readouterr()
    assert "401" in refused.err and token[:-4] not in refused.err + refused.out
    monkeypatch.setenv("CONVOY_API_TOKEN", token)
    monkeypatch.setenv("CONVOY_SERVER", "http://example.com")
    assert importer.main([str(episodes)]) == 1
    assert "https origin" in capsys.readouterr().err


def frame_image(episodes: Path, index: int) -> str:
    return base64.b64encode((episodes / "episode-000" / "frames" / f"{index:04d}.png").read_bytes()).decode()


def test_the_importer_checks_the_replay_format_before_uploading(tmp_path, capsys):
    importer = load_importer()
    episodes = tmp_path / "episodes"
    importer.write_example(episodes, episodes=1, steps=3)
    folder = episodes / "episode-000" / "frames"
    (folder / "0002.json").unlink()
    assert importer.main([str(episodes), "--dry-run"]) == 1
    assert "expected frames 0 to 3" in capsys.readouterr().err
    assert importer.main([str(tmp_path / "missing")]) == 1
    assert importer.main([str(episodes), "--write-example"]) == 1  # never over existing files
    assert "is not empty" in capsys.readouterr().err
    big = tmp_path / "big"
    importer.write_example(big, episodes=1, steps=2)
    frame = json.loads((big / "episode-000" / "frames" / "0001.json").read_text())
    frame["image_png_base64"] = base64.b64encode(b"\0" * (importer.MAX_IMAGE + 1)).decode()
    (big / "episode-000" / "frames" / "0001.json").write_text(json.dumps(frame))
    assert importer.main([str(big), "--dry-run"]) == 1
    assert "image exceeds" in capsys.readouterr().err
