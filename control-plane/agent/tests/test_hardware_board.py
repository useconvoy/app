"""Board-observed inventory and sensor robustness (Jetson Orin Nano Developer Kit Super, L4T 36.4.7)."""

from __future__ import annotations

from pathlib import Path

from convoy_agent import compat, hardware

BOARD_L4T_LINE = (
    "# R36 (release), REVISION: 4.7, GCID: 38968081, BOARD: generic, EABI: aarch64, DATE: Wed Aug  6 20:23:07 UTC 2025\n"
    "# KERNEL_VARIANT: oot\nTARGET_USERSPACE_LIB_DIR=nvidia\nTARGET_USERSPACE_LIB_DIR_PATH=usr/lib/aarch64-linux-gnu/nvidia\n"
)


def test_actual_board_l4t_line_parses_to_36_4_7():
    assert hardware.parse_l4t_line(BOARD_L4T_LINE) == (36, "36.4.7")
    assert hardware.parse_l4t_line("# R36 (release), REVISION: 5.2, GCID: 1") == (36, "36.5.2")
    assert hardware.parse_l4t_line("garbage") is None


def test_board_tuple_is_an_allowed_candidate_track_locally():
    inv = {"simulated": False, "l4t_release": "36.4.7", "cuda_version": "12.6.11"}
    c = compat.classify_local(inv)
    assert (
        c["policy"] == "allowed" and c["jetpack"] is None and c["l4t"] == "36.4.7"
    )  # JetPack not established
    # the original pin is untouched, an unknown release stays unverified, a CUDA major mismatch too
    assert compat.classify_local({"l4t_release": "36.5.2", "cuda_version": "12.6.68"})["policy"] == "allowed"
    assert (
        compat.classify_local({"l4t_release": "36.4.4", "cuda_version": "12.6.11"})["policy"] == "unverified"
    )
    assert (
        compat.classify_local({"l4t_release": "36.4.7", "cuda_version": "13.0.1"})["policy"] == "unverified"
    )


def _zone(base: Path, n: int, typ: str | None, temp: bytes | None) -> Path:
    z = base / f"thermal_zone{n}"
    z.mkdir()
    if typ is not None:
        (z / "type").write_text(typ)
    if temp is not None:
        (z / "temp").write_bytes(temp)
    return z


def test_one_unreadable_thermal_zone_never_hides_the_others(tmp_path, monkeypatch):
    _zone(tmp_path, 0, "cpu-thermal", b"41500\n")
    _zone(tmp_path, 1, "gpu-thermal", b"40000\n")
    _zone(tmp_path, 2, "tj-thermal", None)  # attribute absent
    _zone(tmp_path, 3, "pmic-thermal", b"")  # empty answer
    _zone(tmp_path, 4, "cv0-thermal", b"not-a-number")
    broken = _zone(tmp_path, 5, "soc0-thermal", b"12000")

    real = hardware.read_sysfs_text

    def failing(path: Path, limit: int = 256):
        if path == broken / "temp":
            raise TypeError("can't concat NoneType to bytes")  # what pathlib.read_text raised on L4T
        return real(path, limit)

    monkeypatch.setattr(hardware, "read_sysfs_text", failing)
    temps = hardware.read_thermal(tmp_path)
    assert temps == {"cpu-thermal": 41.5, "gpu-thermal": 40.0}
    for absent in ("tj-thermal", "pmic-thermal", "cv0-thermal", "soc0-thermal"):
        assert absent not in temps  # unknown, not 0


def test_sample_survives_a_raising_sensor_and_marks_it_unknown(tmp_path, monkeypatch):
    def boom():
        raise TypeError("can't concat NoneType to bytes")

    monkeypatch.setattr(hardware, "read_thermal", boom)
    # Exercise sensor-family isolation without relying on the host having Linux /proc/meminfo.
    monkeypatch.setattr(
        hardware,
        "read_meminfo",
        lambda: {
            "mem_total_mb": 8192.0,
            "mem_available_mb": 4096.0,
            "swap_total_mb": 0.0,
            "swap_free_mb": 0.0,
        },
    )
    s = hardware.Sensors(str(tmp_path))
    s.tegrastats = None
    sample = s.sample("ready")
    assert sample["temp_max_c"] is None and sample["temps"] == {}
    assert sample["mem_total_mb"] == 8192.0  # the other families still report
    assert sample["disk_free_mb"] is not None
    assert sample["runtime_state"] == "ready"
    assert sample["sensor_errors"] == {"thermal": "TypeError: can't concat NoneType to bytes"}


def test_eagain_zone_is_skipped_by_a_real_nonblocking_read(tmp_path, monkeypatch):
    """The board's cv0/cv1/cv2 zones answer os.read with BlockingIOError (EAGAIN, errno 11)."""
    import os

    _zone(tmp_path, 0, "cpu-thermal", b"41500\n")
    cv = _zone(tmp_path, 2, "cv0-thermal", b"0")
    rfd, wfd = os.pipe()
    os.set_blocking(rfd, False)  # empty non-blocking pipe: os.read raises BlockingIOError like the zone
    real_open = os.open

    def fake_open(path, flags, *a, **kw):
        if str(path) == str(cv / "temp"):
            return rfd
        return real_open(path, flags, *a, **kw)

    monkeypatch.setattr(hardware.os, "open", fake_open)
    try:
        assert hardware.read_sysfs_text(cv / "temp") is None
        assert hardware.read_thermal(tmp_path) == {"cpu-thermal": 41.5}
    finally:
        os.close(wfd)
        try:
            os.close(rfd)
        except OSError:
            pass  # already closed by read_sysfs_text


BOARD_INV = {
    "arch": "aarch64",
    "l4t_release": "36.4.7",
    "cuda_version": "12.6.11",
    "compute_capability": "8.7",
}
T3647 = {
    "arch": "aarch64",
    "os": "linux",
    "backend": "cuda",
    "compute_capability": "8.7",
    "l4t": "36.4.7",
    "cuda": "12.6",
}
T3652 = {**T3647, "jetpack": "6.2.3", "l4t": "36.5.2"}


def test_device_side_tuple_gate_both_directions_and_unknown():
    assert compat.tuple_mismatch(T3647, BOARD_INV) == (None, [])
    assert "36.5.2" in compat.tuple_mismatch(T3652, BOARD_INV)[0]
    assert "36.4.7" in compat.tuple_mismatch(T3647, {**BOARD_INV, "l4t_release": "36.5.2"})[0]
    assert "unknown" in compat.tuple_mismatch(T3647, {"arch": "aarch64"})[0]
    reason, _ = compat.tuple_mismatch(
        T3647, {k: v for k, v in BOARD_INV.items() if k != "compute_capability"}
    )
    assert reason and "not measured" in reason


def test_nvidia_smi_compute_capability_is_measured_not_assumed(tmp_path):
    smi = tmp_path / "nvidia-smi"
    smi.write_text("#!/bin/sh\necho 'Orin (nvgpu), 8.7, [N/A]'\n")
    smi.chmod(0o755)
    assert hardware.query_gpu(str(smi)) == {"gpu_name": "Orin (nvgpu)", "compute_capability": "8.7"}
    bad = tmp_path / "nvidia-smi-bad"
    bad.write_text("#!/bin/sh\necho 'No devices were found' >&2; exit 6\n")
    bad.chmod(0o755)
    assert hardware.query_gpu(str(bad)) == {"gpu_name": None, "compute_capability": None}
    assert hardware.query_gpu(None) == {"gpu_name": None, "compute_capability": None}
    assert hardware.simulated_inventory()["compute_capability"] is None
