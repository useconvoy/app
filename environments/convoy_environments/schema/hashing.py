"""Canonical hashing for the certified tuple's environment components.

`manifest_hash` covers what a connection can do; `policy_hash` covers what an
environment permits. Both feed the certified tuple
(agent_version, model_id, prompt_hash, tool_manifest_hashes, policy_hash,
eval_set_version) — so the canonical form here is a wire contract: key order,
separators, and float handling must never change once logs exist.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    """Deterministic JSON: sorted keys, compact separators, no NaN/Infinity."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, ensure_ascii=True)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_hash(manifest: Any) -> str:
    """Hash of a connection manifest (a ConnectionManifest model or plain dict)."""
    if hasattr(manifest, "model_dump"):
        manifest = manifest.model_dump(exclude_none=True)
    return sha256_hex(canonical_json(manifest))


def policy_hash(
    backing_type: str,
    connection_policies: Any,
    browser_policy: Any = None,
    sandbox_template: str = "",
    data_namespace: str = "",
) -> str:
    """Hash of everything about an environment that affects agent behavior.

    connection_policies: iterable of (connection_id, manifest_hash,
    tool_allowlist, gate_overrides) — sorted here so caller order is irrelevant.
    """
    rows = []
    for cp in connection_policies:
        if hasattr(cp, "model_dump"):
            cp = cp.model_dump(exclude_none=True)
        rows.append(
            {
                "connectionId": cp["connectionId"],
                "manifestHash": cp["manifestHash"],
                "toolAllowlist": sorted(cp.get("toolAllowlist") or []),
                "promoteOverrides": sorted(cp.get("promoteOverrides") or []),
            }
        )
    rows.sort(key=lambda r: r["connectionId"])
    if browser_policy is not None and hasattr(browser_policy, "model_dump"):
        browser_policy = browser_policy.model_dump(exclude_none=True)
    payload = {
        "backingType": backing_type,
        "connections": rows,
        "browserPolicy": browser_policy,
        # Binding-seam fields: both affect run behavior, so both are
        # hash-relevant (folded in Aug 2026, before anything was certified).
        "sandboxTemplate": sandbox_template,
        "dataNamespace": data_namespace,
    }
    return sha256_hex(canonical_json(payload))
