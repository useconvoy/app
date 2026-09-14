"""Two allowed CUDA 12.6 / SM 8.7 tracks: the original L4T 36.5.2 pin and the board-observed 36.4.7."""

from __future__ import annotations

from conftest import WEB
from convoy_server.compat import classify
from convoy_server.services.catalog import (
    CUDA_TARGET,
    CUDA_TRACKS,
    DEFAULT_CMAKE,
    DEFAULT_TRACK,
    cuda_target,
    track_for_target,
)

BOARD_INVENTORY = {
    "arch": "aarch64",
    "l4t_release": "36.4.7",
    "cuda_version": "12.6.11",
    "jetson_model": "NVIDIA Jetson Orin Nano Engineering Reference Developer Kit Super",
}
BOARD_L4T_LINE = "# R36 (release), REVISION: 4.7, GCID: 38968081, BOARD: generic, EABI: aarch64, DATE: Wed Aug  6 20:23:07 UTC 2025"
PIN_L4T_LINE = "# R36 (release), REVISION: 5.2, GCID: 1, BOARD: generic, EABI: aarch64"
COMMIT = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"


def test_board_tuple_classifies_as_an_allowed_untested_candidate():
    c = classify(BOARD_INVENTORY)
    assert (
        c["policy"] == "allowed" and c["evidence"] == "untested" and c["jetpack"] is None
    )  # not established
    assert "board-observed" in c["source_verified"]
    # the original pin and the CUDA 13 row are untouched
    assert classify({"l4t_release": "36.5.2", "cuda_version": "12.6.68"})["policy"] == "allowed"
    assert classify({"l4t_release": "39.2.1", "cuda_version": "13.0.1"})["policy"] == "unverified"
    assert classify({"l4t_release": "36.4.4", "cuda_version": "12.6.11"})["policy"] == "unverified"


def test_track_table_shares_everything_but_the_l4t_release():
    assert DEFAULT_TRACK == "jp623" and cuda_target(None) == CUDA_TARGET == CUDA_TRACKS["jp623"]
    a, b = CUDA_TRACKS["jp623"], CUDA_TRACKS["l4t3647"]
    assert "jetpack" not in b and {k for k in b if a[k] != b[k]} == {
        "l4t"
    }  # no JetPack version is asserted for the board
    assert b["l4t"] == "36.4.7" and b["cuda"] == "12.6" and b["compute_capability"] == "8.7"
    assert track_for_target(CUDA_TARGET) == "jp623" and track_for_target(b) == "l4t3647"
    assert track_for_target({**b, "l4t": "36.4.4"}) is None


def _recipe(admin, name, **extra):
    return admin.post(
        "/api/v1/recipes",
        json={"name": name, "commit": COMMIT, "tag": "v0.4.0", "backend": "cuda", **extra},
        headers=WEB,
    )


def _receipt(l4t_line, sha):
    return {
        "archive_sha256": sha,
        "archive_size": 100,
        "files": [{"path": "bin/llama-server", "sha256": "ab" * 32, "size": 10}],
        "provenance": {
            "commit": COMMIT,
            "cmake_flags": list(DEFAULT_CMAKE),
            "cuda_version": "12.6.11",
            "l4t_release": l4t_line,
            "track": "l4t3647",
        },
    }


def test_recipe_track_selector_and_receipt_isolation_between_tracks(app, admin):
    r = _recipe(admin, "board-track", track="l4t3647")
    assert r.status_code == 201, r.text
    board = r.json()
    assert (
        board["target"]["l4t"] == "36.4.7"
        and board["track"] == "l4t3647"
        and "jetpack" not in board["target"]
    )
    pin = _recipe(admin, "pinned").json()  # no track: the original 36.5.2 pin, unchanged behaviour
    assert pin["target"] == CUDA_TARGET and pin["track"] == "jp623"
    assert pin["digest"] != board["digest"]
    assert _recipe(admin, "both", track="l4t3647", target={"l4t": "x"}).status_code == 422
    assert _recipe(admin, "unknown", track="jp999").status_code in (400, 422)
    assert _recipe(admin, "cpu-track", track="l4t3647", backend="cpu").status_code == 422
    # the same board-built receipt registers under the board track and is refused under the pin
    ok = admin.post(
        "/api/v1/runtime-artifacts",
        json={
            "recipe_id": board["id"],
            "receipt": _receipt(BOARD_L4T_LINE, "cd" * 32),
            "scope": "fleet",
            "storage": "device",
        },
        headers=WEB,
    )
    assert ok.status_code == 201, ok.text
    assert ok.json()["provenance"]["track"] == "l4t3647"
    refused = admin.post(
        "/api/v1/runtime-artifacts",
        json={
            "recipe_id": pin["id"],
            "receipt": _receipt(BOARD_L4T_LINE, "ef" * 32),
            "scope": "fleet",
            "storage": "device",
        },
        headers=WEB,
    )
    assert refused.status_code == 409 and "36.5.2" in refused.text
    # and a 36.5.2 receipt is refused under the board track
    refused2 = admin.post(
        "/api/v1/runtime-artifacts",
        json={
            "recipe_id": board["id"],
            "receipt": _receipt(PIN_L4T_LINE, "12" * 32),
            "scope": "fleet",
            "storage": "device",
        },
        headers=WEB,
    )
    assert refused2.status_code == 409 and "36.4.7" in refused2.text
    base = admin.get("/api/v1/baseline", headers=WEB).json()
    assert base["tracks"]["l4t3647"]["l4t"] == "36.4.7" and base["default_track"] == "jp623"


def test_build_script_track_selector_prints_the_tuple_without_touching_the_host(tmp_path):
    import subprocess
    from pathlib import Path

    script = Path(__file__).resolve().parents[2] / "scripts" / "jetson" / "build-llama-cpp.sh"
    out = subprocess.run(
        ["bash", str(script), "--track", "l4t3647", "--print-track"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    kv = dict(line.split("=", 1) for line in out.strip().splitlines())
    assert (
        kv["track"] == "l4t3647"
        and kv["l4t"] == "36.4.7"
        and kv["cuda"] == "12.6"
        and kv["compute_capability"] == "8.7"
    )
    assert kv["commit"] == COMMIT and kv["cmake_flags"].split() == DEFAULT_CMAKE
    default = subprocess.run(
        ["bash", str(script), "--print-track"], capture_output=True, text=True, check=True
    ).stdout
    assert "l4t=36.5.2" in default and "track=jp623" in default
    bad = subprocess.run(
        ["bash", str(script), "--track", "jp999", "--print-track"], capture_output=True, text=True
    )
    assert bad.returncode == 2 and "unknown track" in bad.stderr
