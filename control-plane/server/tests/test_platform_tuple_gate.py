"""Tracks that share a hardware profile are told apart by the exact platform tuple at dispatch and grant,
and the package-only build reuse validates a real CMake tree layout without executing anything."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

from conftest import WEB, FakeAgent, enrollment_token
from convoy_server.compat import tuple_mismatch
from convoy_server.services.catalog import DEFAULT_CMAKE
from helpers import heartbeat

COMMIT = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
BOARD = {
    "arch": "aarch64",
    "l4t_release": "36.4.7",
    "cuda_version": "12.6.11",
    "compute_capability": "8.7",
    "gpu_name": "Orin (nvgpu)",
}
PIN = {"arch": "aarch64", "l4t_release": "36.5.2", "cuda_version": "12.6.68", "compute_capability": "8.7"}
T3647 = {
    "arch": "aarch64",
    "os": "linux",
    "backend": "cuda",
    "compute_capability": "8.7",
    "l4t": "36.4.7",
    "cuda": "12.6",
}
T3652 = {**T3647, "jetpack": "6.2.3", "l4t": "36.5.2"}


def test_tuple_mismatch_both_directions_unknown_and_sm():
    assert tuple_mismatch(T3647, BOARD) == (None, [])
    assert tuple_mismatch(T3652, PIN) == (None, [])
    assert "36.5.2" in tuple_mismatch(T3652, BOARD)[0] and "36.4.7" in tuple_mismatch(T3652, BOARD)[0]
    assert "36.4.7" in tuple_mismatch(T3647, PIN)[0]
    assert "unknown" in tuple_mismatch(T3647, {})[0]
    assert "CUDA version" in tuple_mismatch(T3647, {"arch": "aarch64", "l4t_release": "36.4.7"})[0]
    assert "CUDA 12.6 differs" in tuple_mismatch(T3647, {**BOARD, "cuda_version": "13.0.1"})[0]
    assert "arch" in tuple_mismatch(T3647, {**BOARD, "arch": "x86_64"})[0]
    assert (
        "compute capability 8.7 differs" in tuple_mismatch(T3647, {**BOARD, "compute_capability": "8.9"})[0]
    )
    reason, _ = tuple_mismatch(T3647, {k: v for k, v in BOARD.items() if k != "compute_capability"})
    assert (
        reason and "not measured" in reason and "not admitted" in reason
    )  # a pinned SM is never silently admitted
    assert tuple_mismatch({}, {}) == (None, []) and tuple_mismatch("sim", BOARD) == (
        None,
        [],
    )  # no CUDA tuple pinned


def _physical_release(admin, track, name):
    rec = admin.post(
        "/api/v1/recipes",
        json={
            "name": f"rcp-{name}",
            "commit": COMMIT,
            "tag": "v0.4.0",
            "backend": "cuda",
            **({"track": track} if track else {}),
        },
        headers=WEB,
    ).json()
    l4t = (
        "# R36 (release), REVISION: 4.7, GCID: 38968081, BOARD: generic"
        if track == "l4t3647"
        else "# R36 (release), REVISION: 5.2, GCID: 1, BOARD: generic"
    )
    receipt = {
        "archive_sha256": hashlib.sha256(name.encode()).hexdigest(),
        "archive_size": 100,
        "files": [{"path": "bin/llama-server", "sha256": "ab" * 32, "size": 10}],
        "provenance": {
            "commit": COMMIT,
            "cmake_flags": list(DEFAULT_CMAKE),
            "cuda_version": "12.6.11",
            "l4t_release": l4t,
        },
    }
    art = admin.post(
        "/api/v1/runtime-artifacts",
        json={"recipe_id": rec["id"], "receipt": receipt, "scope": "fleet", "storage": "device"},
        headers=WEB,
    )
    assert art.status_code == 201, art.text
    rel = admin.post(
        "/api/v1/releases",
        json={
            "name": f"rel-{name}",
            "version": "1",
            "recipe_id": rec["id"],
            "runtime_artifact_id": art.json()["id"],
            "model": {
                "source": "supplied",
                "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
                "revision": "91cad51170dc346986eccefdc2dd33a9da36ead9",
                "supplied_files": [
                    {
                        "path": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
                        "size": 1117320736,
                        "sha256": "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
                    }
                ],
            },
        },
        headers=WEB,
    )
    assert rel.status_code == 201, rel.text
    assert rel.json()["build_status"] == "ready"
    return rel.json()


def _physical_device(app, admin, hardware):
    a = FakeAgent(app, name="orin-" + (hardware.get("l4t_release") or "unknown"))
    assert (
        a.enroll(enrollment_token(admin, simulated=False), simulated=False, hardware=hardware).status_code
        == 200
    )
    heartbeat(a, telemetry={"mem_total_mb": 7619.0, "mem_available_mb": 6400.0, "disk_free_mb": 140000.0})
    return a


def _deploy(admin, a, rel):
    return admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": rel["id"], "bootstrap": True},
        headers=WEB,
    )


def test_dispatch_refuses_cross_track_releases_and_unknown_tuples_in_both_directions(app, admin):
    rel_pin = _physical_release(admin, None, "pin")  # L4T 36.5.2 track (jp623)
    rel_board = _physical_release(admin, "l4t3647", "board")
    board = _physical_device(app, admin, BOARD)
    pin_dev = _physical_device(app, admin, PIN)
    unknown = _physical_device(app, admin, {"arch": "aarch64"})
    r = _deploy(admin, board, rel_pin)
    assert r.status_code == 409 and "36.5.2" in r.text and "36.4.7" in r.text, r.text
    r = _deploy(admin, pin_dev, rel_board)
    assert r.status_code == 409 and "36.4.7" in r.text, r.text
    r = _deploy(admin, unknown, rel_board)
    assert r.status_code == 409 and "unknown" in r.text, r.text
    no_sm = _physical_device(app, admin, {k: v for k, v in BOARD.items() if k != "compute_capability"})
    r = _deploy(admin, no_sm, rel_board)
    assert r.status_code == 409 and "not measured" in r.text, r.text
    # matching tracks dispatch (device state permitting): the gate itself passes
    ok = _deploy(admin, board, rel_board)
    assert ok.status_code == 201, ok.text
    ok2 = _deploy(admin, pin_dev, rel_pin)
    assert ok2.status_code == 201, ok2.text


def _fixture_tree(
    tmp: Path, *, flags, cuda_ver="12.6.68", cxx_ver="11.4.0", cxx_reports=None, with_binary=True
):
    """Real CMake 3.22 layout: FILEPATH compiler entries in CMakeCache.txt, versions only in
    CMakeFiles/3.22.1/CMake<LANG>Compiler.cmake (as observed on the board)."""
    src = tmp / "llama.cpp"
    src.mkdir(parents=True, exist_ok=True)
    build = tmp / "build"
    (build / "bin").mkdir(parents=True, exist_ok=True)
    (build / "CMakeFiles" / "3.22.1").mkdir(parents=True, exist_ok=True)
    bindir = tmp / "hostbin"
    bindir.mkdir(exist_ok=True)
    nvcc = bindir / "nvcc"
    nvcc.write_text("#!/bin/sh\necho nvcc\n")
    cxx = bindir / "c++"
    cxx.write_text(f'#!/bin/sh\n[ "$1" = -dumpfullversion ] && echo {cxx_reports or cxx_ver}\n')
    for f in (nvcc, cxx):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    lines = [
        f"CMAKE_HOME_DIRECTORY:INTERNAL={src}",
        f"CMAKE_CUDA_COMPILER:FILEPATH={nvcc}",
        f"CMAKE_CXX_COMPILER:FILEPATH={cxx}",
        f"CMAKE_C_COMPILER:FILEPATH={bindir}/cc",
    ]
    for fl in flags:
        name, _, value = fl[2:].partition("=")
        lines.append(f"{name}:UNINITIALIZED={value}")
    (build / "CMakeCache.txt").write_text("\n".join(lines) + "\n")
    (build / "CMakeFiles" / "3.22.1" / "CMakeCUDACompiler.cmake").write_text(
        f'set(CMAKE_CUDA_COMPILER "{nvcc}")\nset(CMAKE_CUDA_COMPILER_VERSION "{cuda_ver}")\n'
    )
    (build / "CMakeFiles" / "3.22.1" / "CMakeCXXCompiler.cmake").write_text(
        f'set(CMAKE_CXX_COMPILER "{cxx}")\nset(CMAKE_CXX_COMPILER_VERSION "{cxx_ver}")\n'
    )
    (build / "CMakeFiles" / "3.22.1" / "CMakeCCompiler.cmake").write_text(
        f'set(CMAKE_C_COMPILER_VERSION "{cxx_ver}")\n'
    )
    if with_binary:
        b = build / "bin" / "llama-server"
        b.write_text("#!/bin/sh\n")
        b.chmod(0o755)
    return build, src, nvcc


def _check(build, src, nvcc, *, nvcc_version="12.6.68", flags=DEFAULT_CMAKE):
    script = Path(__file__).resolve().parents[2] / "scripts" / "jetson" / "reuse-build-check.sh"
    return subprocess.run(
        [
            "bash",
            str(script),
            "--build",
            str(build),
            "--src",
            str(src),
            "--nvcc",
            str(nvcc),
            "--nvcc-version",
            nvcc_version,
            "--",
            *flags,
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )


def test_reuse_build_check_reads_the_real_cmake_layout_by_inspection_only(tmp_path):
    build, src, nvcc = _fixture_tree(tmp_path, flags=DEFAULT_CMAKE)
    r = _check(build, src, nvcc)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "nvcc 12.6.68" in r.stdout and "C++ 11.4.0" in r.stdout
    facts = dict(
        line.split("=", 1) for line in r.stdout.splitlines() if "=" in line and not line.startswith(" ")
    )
    assert (
        facts["cxx_version"] == "11.4.0"
        and facts["cuda_compiler_version"] == "12.6.68"
        and facts["cxx_compiler"].endswith("/c++")
    )
    # the caller's major.minor is NOT accepted as the full nvcc identity
    assert (
        "detected nvcc 12.6.68, the host nvcc is 12.6" in _check(build, src, nvcc, nvcc_version="12.6").stderr
    )
    # wrong recipe value, wrong detected nvcc, C++ compiler that no longer reports the detected version,
    # foreign source, missing binary: each refused
    b3, s3, n3 = _fixture_tree(
        tmp_path / "b3", flags=[f.replace("=87", "=89") if "ARCH" in f else f for f in DEFAULT_CMAKE]
    )
    assert "CMAKE_CUDA_ARCHITECTURES='89'" in _check(b3, s3, n3).stderr
    b4, s4, n4 = _fixture_tree(tmp_path / "b4", flags=DEFAULT_CMAKE, cuda_ver="12.4.131")
    assert "detected nvcc 12.4.131" in _check(b4, s4, n4).stderr
    b5, s5, n5 = _fixture_tree(tmp_path / "b5", flags=DEFAULT_CMAKE, cxx_reports="12.3.0")
    assert "reports 12.3.0 now, CMake detected 11.4.0" in _check(b5, s5, n5).stderr
    b6, s6, n6 = _fixture_tree(tmp_path / "b6", flags=DEFAULT_CMAKE)
    assert "not the pinned source" in _check(b6, tmp_path / "elsewhere", n6).stderr
    b7, s7, n7 = _fixture_tree(tmp_path / "b7", flags=DEFAULT_CMAKE, with_binary=False)
    assert "compile did not finish" in _check(b7, s7, n7).stderr


def test_measured_sm_survives_real_enrollment_and_heartbeat_ingestion_and_gates_dispatch(app, admin):
    """The real protocol: hardware arrives in the enrollment body and again in periodic reports
    (typed_hardware). gpu_name/compute_capability must survive both so the tuple gate can see SM 8.7;
    a report without SM, with a mismatched SM, or with a malformed SM stays refused."""
    rel_board = _physical_release(admin, "l4t3647", "ingest")
    a = FakeAgent(app, name="orin-real")
    assert (
        a.enroll(enrollment_token(admin, simulated=False), simulated=False, hardware=BOARD).status_code == 200
    )
    # a heartbeat carrying the full inventory (the agent sends it every 20th report) goes through ingestion
    heartbeat(
        a,
        hardware=BOARD,
        telemetry={"mem_total_mb": 7619.0, "mem_available_mb": 6400.0, "disk_free_mb": 140000.0},
    )
    hw = admin.get(f"/api/v1/devices/{a.device_id}", headers=WEB).json()["hardware"]
    assert (
        hw["compute_capability"] == "8.7"
        and hw["gpu_name"] == "Orin (nvgpu)"
        and hw["l4t_release"] == "36.4.7"
    )
    ok = _deploy(admin, a, rel_board)
    assert ok.status_code == 201, ok.text
    # the same device later reporting without a measured SM is refused (nothing is filled from a profile)
    b = FakeAgent(app, name="orin-nosm")
    assert (
        b.enroll(enrollment_token(admin, simulated=False), simulated=False, hardware=BOARD).status_code == 200
    )
    heartbeat(
        b,
        hardware={k: v for k, v in BOARD.items() if k != "compute_capability"},
        telemetry={"mem_total_mb": 7619.0, "mem_available_mb": 6400.0, "disk_free_mb": 140000.0},
    )
    assert (
        admin.get(f"/api/v1/devices/{b.device_id}", headers=WEB).json()["hardware"].get("compute_capability")
        is None
    )
    r = _deploy(admin, b, rel_board)
    assert r.status_code == 409 and "not measured" in r.text, r.text
    # mismatched and malformed SM through ingestion: refused too (malformed is stored as unmeasured, never coerced)
    c = FakeAgent(app, name="orin-sm89")
    assert (
        c.enroll(
            enrollment_token(admin, simulated=False),
            simulated=False,
            hardware={**BOARD, "compute_capability": "8.9"},
        ).status_code
        == 200
    )
    heartbeat(
        c,
        hardware={**BOARD, "compute_capability": "8.9"},
        telemetry={"mem_total_mb": 7619.0, "mem_available_mb": 6400.0, "disk_free_mb": 140000.0},
    )
    r = _deploy(admin, c, rel_board)
    assert r.status_code == 409 and "8.9" in r.text, r.text
    d = FakeAgent(app, name="orin-malformed")
    assert (
        d.enroll(enrollment_token(admin, simulated=False), simulated=False, hardware=BOARD).status_code == 200
    )
    heartbeat(
        d,
        hardware={**BOARD, "compute_capability": "8,7"},
        telemetry={"mem_total_mb": 7619.0, "mem_available_mb": 6400.0, "disk_free_mb": 140000.0},
    )
    assert (
        admin.get(f"/api/v1/devices/{d.device_id}", headers=WEB).json()["hardware"].get("compute_capability")
        is None
    )
    r = _deploy(admin, d, rel_board)
    assert r.status_code == 409 and "not measured" in r.text, r.text
