"""The device agent's systemd unit: hardening must never imply a closed device policy on the Jetson
(ProtectClock does: DeviceAllow=char-rtc r), while the clock protection stays explicit."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

UNIT = Path(__file__).resolve().parents[2] / "deploy" / "systemd" / "convoy-agent.service"


def _directives() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in UNIT.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        k, _, v = line.partition("=")
        out.setdefault(k.strip(), []).append(v.strip())
    return out


def test_unit_keeps_gpu_device_access_and_explicit_clock_protection():
    d = _directives()
    assert d["ProtectClock"] == ["no"]  # yes would imply DeviceAllow=char-rtc r -> closed device policy
    assert "DeviceAllow" not in d and "DevicePolicy" not in d and "PrivateDevices" not in d
    assert d["CapabilityBoundingSet"] == ["~CAP_SYS_TIME CAP_WAKE_ALARM"]
    assert d["SystemCallFilter"] == ["~@clock"]
    # the rest of the hardening is untouched
    for k, v in {
        "NoNewPrivileges": "yes",
        "ProtectSystem": "strict",
        "PrivateTmp": "yes",
        "ProtectHome": "yes",
        "ProtectKernelTunables": "yes",
        "ProtectKernelModules": "yes",
        "ProtectControlGroups": "yes",
        "RestrictNamespaces": "yes",
        "SystemCallArchitectures": "native",
        "User": "convoy-agent",
        "SupplementaryGroups": "video",
        "MemoryMax": "6G",
        "RestrictAddressFamilies": "AF_INET AF_INET6 AF_UNIX",
    }.items():
        assert d[k] == [v], k
    if shutil.which("systemd-analyze"):
        r = subprocess.run(
            ["systemd-analyze", "verify", "--man=no", str(UNIT)], capture_output=True, text=True
        )
        assert "Unknown" not in r.stderr and "Failed to parse" not in r.stderr, r.stderr
