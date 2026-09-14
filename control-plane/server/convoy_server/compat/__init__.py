from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def matrix() -> dict[str, Any]:
    return json.loads((Path(__file__).parent / "support_matrix.json").read_text())


def classify(inventory: dict[str, Any] | None) -> dict[str, Any]:
    """Map a device-reported inventory (l4t release string, cuda version, arch) to a policy row.
    Unknown inventory stays unknown; nothing is assumed."""
    inv = inventory or {}
    l4t = inv.get("l4t_release")
    cuda = inv.get("cuda_version")
    if not l4t:
        return {
            "policy": "unknown",
            "evidence": "untested",
            "reason": "L4T release not reported",
            "l4t": None,
            "cuda": cuda,
        }
    for row in matrix()["rows"]:
        if str(l4t) == row["l4t"]:
            cuda_ok = cuda is None or str(cuda).startswith(row["cuda"])
            return {
                "policy": row["policy"] if cuda_ok else "unverified",
                "evidence": row["evidence"],
                "jetpack": row["jetpack"],
                "l4t": l4t,
                "cuda": cuda,
                "reason": None if cuda_ok else f"CUDA {cuda} does not match the row's {row['cuda']}",
                "source": row["source"],
                "source_verified": row["source_verified"],
            }
    return {
        "policy": "unverified",
        "evidence": "untested",
        "jetpack": None,
        "l4t": l4t,
        "cuda": cuda,
        "reason": "L4T release not in the support matrix",
    }


TUPLE_KEYS = ("arch", "l4t", "cuda", "compute_capability")


def tuple_mismatch(
    target: dict[str, Any] | None, inventory: dict[str, Any] | None
) -> tuple[str | None, list[str]]:
    """Compare a release's platform target tuple with the tuple a physical device REPORTED.
    Returns (reason, warnings): reason is None only when the device proved the architecture, the
    full L4T release string and the CUDA major.minor of the target. compute_capability must be
    measured by the device (`nvidia-smi --query-gpu=compute_cap`) whenever the target pins one; an
    unmeasured SM is refused, never silently admitted. Two tracks that
    share a hardware profile (L4T 36.5.2 and 36.4.7) are told apart here."""
    t = target if isinstance(target, dict) else {}
    inv = inventory or {}
    if not t.get("l4t") and not t.get("cuda"):
        return None, []  # no CUDA tuple pinned (simulated/cpu recipe): nothing to prove
    warnings: list[str] = []
    arch, l4t, cuda = inv.get("arch"), inv.get("l4t_release"), inv.get("cuda_version")
    if not arch or not l4t or not cuda:
        missing = [k for k, v in (("arch", arch), ("L4T release", l4t), ("CUDA version", cuda)) if not v]
        return f"device platform tuple unknown: {', '.join(missing)} not reported", warnings
    if t.get("arch") and str(arch) != str(t["arch"]):
        return f"release target arch {t['arch']} differs from the device's {arch}", warnings
    if t.get("l4t") and str(l4t) != str(t["l4t"]):
        return f"release target L4T {t['l4t']} differs from the device's {l4t}", warnings
    cuda_mm = ".".join(str(cuda).split(".")[:2])
    if t.get("cuda") and cuda_mm != str(t["cuda"]):
        return f"release target CUDA {t['cuda']} differs from the device's {cuda_mm} ({cuda})", warnings
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
                f"release target compute capability {t['compute_capability']} differs from the device's {sm}",
                warnings,
            )
    return None, warnings
