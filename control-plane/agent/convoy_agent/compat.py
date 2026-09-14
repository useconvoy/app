"""Local view of the platform policy (mirrors the server's compat matrix; the server is authoritative)."""

from __future__ import annotations

from typing import Any

MATRIX = [
    {"jetpack": "6.2.3", "l4t": "36.5.2", "cuda": "12.6", "policy": "allowed"},
    # board-observed candidate track l4t3647 (Orin Nano Developer Kit Super, 2026-09-13): the JetPack
    # version is not established (no nvidia-jetpack metapackage; NVIDIA lists JetPack 6.2.2 with L4T 36.5)
    {"jetpack": None, "l4t": "36.4.7", "cuda": "12.6", "policy": "allowed"},
    {"jetpack": "7.2.1", "l4t": "39.2.1", "cuda": "13", "policy": "unverified"},
]


def classify_local(inv: dict[str, Any]) -> dict[str, Any]:
    if inv.get("simulated"):
        return {"policy": "simulated", "reason": "simulated host; no physical qualification"}
    l4t = inv.get("l4t_release")
    if not l4t:
        return {"policy": "unknown", "reason": "L4T release not detected (/etc/nv_tegra_release)"}
    for row in MATRIX:
        if l4t == row["l4t"]:
            cuda = inv.get("cuda_version")
            ok = cuda is None or str(cuda).startswith(row["cuda"])
            return {
                "policy": row["policy"] if ok else "unverified",
                "jetpack": row["jetpack"],
                "l4t": l4t,
                "cuda": cuda,
            }
    return {"policy": "unverified", "l4t": l4t, "reason": "L4T release not in the support matrix"}


def tuple_mismatch(
    target: dict[str, Any] | None, inventory: dict[str, Any] | None
) -> tuple[str | None, list[str]]:
    """Device-side twin of the server's check: (reason, warnings). None reason only when this device's
    reported arch, full L4T release and CUDA major.minor equal the release target's; compute
    capability must be measured (nvidia-smi) whenever the target pins one; unmeasured is refused."""
    t = target if isinstance(target, dict) else {}
    inv = inventory or {}
    if not t.get("l4t") and not t.get("cuda"):
        return None, []
    warnings: list[str] = []
    arch, l4t, cuda = inv.get("arch"), inv.get("l4t_release"), inv.get("cuda_version")
    if not arch or not l4t or not cuda:
        missing = [k for k, v in (("arch", arch), ("L4T release", l4t), ("CUDA version", cuda)) if not v]
        return f"device platform tuple unknown: {', '.join(missing)} not detected", warnings
    if t.get("arch") and str(arch) != str(t["arch"]):
        return f"release target arch {t['arch']} differs from this device's {arch}", warnings
    if t.get("l4t") and str(l4t) != str(t["l4t"]):
        return f"release target L4T {t['l4t']} differs from this device's {l4t}", warnings
    cuda_mm = ".".join(str(cuda).split(".")[:2])
    if t.get("cuda") and cuda_mm != str(t["cuda"]):
        return f"release target CUDA {t['cuda']} differs from this device's {cuda_mm} ({cuda})", warnings
    sm = inv.get("compute_capability")
    if t.get("compute_capability"):
        if sm is None:
            return (
                "compute capability not measured by the device (nvidia-smi --query-gpu=compute_cap unavailable); "
                f"the release pins SM {t['compute_capability']} and an unmeasured SM is not admitted",
                warnings,
            )
        if str(sm) != str(t["compute_capability"]):
            return (
                f"release target compute capability {t['compute_capability']} differs from this device's {sm}",
                warnings,
            )
    return None, warnings
