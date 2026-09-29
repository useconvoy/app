from __future__ import annotations

import io
import tarfile

import pytest
from conftest import WEB
from helpers import enrolled_agent, heartbeat, seed

FIX = {
    "source": "fixture",
    "repo": "convoy-sim/qwen2.5-1.5b-instruct-gguf",
    "revision": "0" * 40,
    "files": ["qwen2.5-1.5b-instruct-q4_k_m.sim.gguf"],
}


def test_release_identity_is_canonical_and_immutable(app, admin):
    s = seed(admin)
    rel = admin.get(f"/api/v1/releases/{s['release_id']}").json()
    spec = rel["spec"]
    assert (
        spec["schema_version"] == 1
        and spec["model"]["file"]["sha256"]
        and spec["runtime"]["commit"] == "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
    )
    assert (
        spec["config"]["ctx_size"] == 2048
        and spec["config"]["n_predict"] == 128
        and spec["config"]["parallel"] == 1
        and spec["config"]["cache_ram_mib"] == 0
    )
    assert "name" not in spec and "notes" not in spec  # descriptive fields outside the identity
    assert "threads" not in spec["config"] and "threads_batch" not in spec["config"]
    # identical content -> 409; same name/version -> 409; no update endpoint exists
    body = {
        "name": "dup",
        "version": "1",
        "model": FIX,
        "recipe_id": s["recipe_id"],
        "runtime_artifact_id": s["artifact_id"],
        "eval_set_id": s["eval_set_id"],
        "profile_id": "simulated-host",
    }
    assert admin.post("/api/v1/releases", json=body, headers=WEB).status_code == 409
    assert admin.patch(
        f"/api/v1/releases/{s['release_id']}", json={"notes": "x"}, headers=WEB
    ).status_code in (404, 405)
    argv = rel["provenance"]["argv_preview"]
    assert (
        "--offline" in argv
        and "--cache-ram" in argv
        and argv[argv.index("--cache-ram") + 1] == "0"
        and "--no-context-shift" in argv
    )
    body.update(name="explicit-cpu-threads", config={"threads": 2, "threads_batch": 2})
    created = admin.post("/api/v1/releases", json=body, headers=WEB)
    assert created.status_code in (200, 201), created.text
    threaded = created.json()
    assert threaded["digest"] != rel["digest"]
    assert threaded["spec"]["config"]["threads"] == threaded["spec"]["config"]["threads_batch"] == 2
    assert {key: value for key, value in threaded["spec"]["config"].items()
            if key not in {"threads", "threads_batch"}} == spec["config"]
    threaded_argv = threaded["provenance"]["argv_preview"]
    assert threaded_argv[threaded_argv.index("--threads") + 1] == "2"
    assert threaded_argv[threaded_argv.index("--threads-batch") + 1] == "2"


def test_invalid_config_rejected(app, admin):
    s = seed(admin)
    body = {
        "name": "bad",
        "version": "1",
        "model": FIX,
        "recipe_id": s["recipe_id"],
        "runtime_artifact_id": s["artifact_id"],
        "profile_id": "simulated-host",
        "config": {"n_predict": 4096},
    }
    r = admin.post("/api/v1/releases", json=body, headers=WEB)
    assert r.status_code in (400, 422) and "n_predict" in r.json()["error"]
    body["config"] = {"parallel": 2}
    assert admin.post("/api/v1/releases", json=body, headers=WEB).status_code in (400, 422)
    body["config"] = {"unknown_setting": 1}
    assert admin.post("/api/v1/releases", json=body, headers=WEB).status_code == 422
    body["config"] = {"temperature": "0.5"}
    assert admin.post("/api/v1/releases", json=body, headers=WEB).status_code == 422
    for field in ("threads", "threads_batch"):
        for value in (None, True, "2", 2.0, 0, 257):
            body["config"] = {field: value}
            assert admin.post("/api/v1/releases", json=body, headers=WEB).status_code == 422


def test_supplied_provenance_and_url_policy(app, admin):
    from convoy_agent.urlpolicy import UrlPolicyError, validate_download_url

    r = admin.post(
        "/api/v1/releases/resolve",
        json={
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
        headers=WEB,
    )
    assert r.status_code == 200 and r.json()["verified"] == "supplied"
    url = r.json()["files"][0]["url"]
    assert (
        url
        == "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    )
    assert validate_download_url(url) == "hf"
    for bad in (
        "https://huggingface.co/Qwen/x/resolve/main/a.gguf",
        "https://hf-mirror.com/Qwen/x/resolve/" + "a" * 40 + "/a.gguf",
        "https://10.0.0.1/a.gguf",
        "http://huggingface.co/Qwen/x/resolve/" + "a" * 40 + "/a.gguf",
        "https://huggingface.co/Qwen/x/resolve/" + "a" * 40 + "/a.bin",
    ):
        with pytest.raises(UrlPolicyError):
            validate_download_url(bad)
    # non-commit revisions and bad hashes are rejected at resolve time
    assert (
        admin.post(
            "/api/v1/releases/resolve",
            json={
                "source": "supplied",
                "repo": "Qwen/x",
                "revision": "main",
                "supplied_files": [{"path": "a.gguf", "size": 1, "sha256": "0" * 64}],
            },
            headers=WEB,
        ).status_code
        == 422
    )
    assert (
        admin.post(
            "/api/v1/releases/resolve",
            json={
                "source": "supplied",
                "repo": "Qwen/x",
                "revision": "a" * 40,
                "supplied_files": [{"path": "../a.gguf", "size": 1, "sha256": "0" * 64}],
            },
            headers=WEB,
        ).status_code
        == 422
    )


def test_plan_gate_validation(app, admin):
    s = seed(admin)
    base = {"name": "p", "release_id": s["release_id"]}
    for gates, msg in (
        ([{"metric": "quality.nope", "op": "min", "limit": 1}], "unknown metric"),
        ([{"metric": "quality.pass_rate", "op": "gte", "limit": 1}], "op must be"),
        ([{"metric": "latency.p95_ms", "op": "max", "limit": 10, "evidence": "sensor_samples"}], "evidence"),
    ):
        r = admin.post("/api/v1/plans", json={**base, "gates": gates}, headers=WEB)
        assert r.status_code in (400, 422), (gates, r.text)
        if r.status_code == 400:
            assert msg in r.json()["error"]
    r = admin.post(
        "/api/v1/plans",
        json={**base, "gates": [{"metric": "thermal.max_c", "op": "max", "limit": 80}]},
        headers=WEB,
    )
    assert r.status_code == 201 and r.json()["gates"][0]["evidence"] == "sensor_samples"
    assert (
        admin.post(
            "/api/v1/plans",
            json={**base, "gates": [{"metric": "thermal.max_c", "op": "max", "limit": 80}]},
            headers=WEB,
        ).status_code
        == 409
    )  # identical plan


def test_eval_set_scorers_restricted(app, admin):
    bad = "\n".join(['{"id":"a","prompt":"x","match":"regex","expected":".*"}'])
    r = admin.post("/api/v1/eval-sets", json={"name": "e", "version": "1", "cases_jsonl": bad}, headers=WEB)
    assert r.status_code == 400 and "match" in r.json()["error"]
    good = '{"id":"a","prompt":"x","match":"label","expected":"yes"}\n{"id":"b","prompt":"y","match":"json_field","field":"k","expected":1}\n'
    r = admin.post("/api/v1/eval-sets", json={"name": "e", "version": "1", "cases_jsonl": good}, headers=WEB)
    assert r.status_code == 201 and r.json()["case_count"] == 2
    assert (
        admin.post(
            "/api/v1/eval-sets", json={"name": "e", "version": "1", "cases_jsonl": good}, headers=WEB
        ).status_code
        == 409
    )


def test_budget_unknown_until_live_report(app, admin):
    s = seed(admin)
    a = enrolled_agent(app, admin)
    b = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert b["verdict"] == "unknown" and b["mem_available_mb"] is None
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 900.0, "disk_free_mb": 20000.0})
    b = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert (
        b["verdict"] == "does_not_fit" and b["items"][1]["mb"] and b["items"][1]["mb"] < 100
    )  # KV cache ~56 MiB
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": None})
    assert (
        admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()["verdict"] == "unknown"
    )
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": 20000.0})
    assert admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()["verdict"] == "fit"


def test_artifact_upload_and_registration(app, admin):
    s = seed(admin)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"#!/bin/sh\nexit 0\n"
        ti = tarfile.TarInfo("bin/llama-server.sim")
        ti.size = len(data)
        tf.addfile(ti, io.BytesIO(data))
    r = admin.post(
        "/api/v1/runtime-artifacts/upload",
        content=buf.getvalue(),
        headers={**WEB, "content-type": "application/gzip"},
    )
    assert r.status_code == 201, r.text
    up = r.json()
    import hashlib

    receipt = {
        "archive_sha256": up["archive_sha256"],
        "archive_size": up["archive_size"],
        "files": [
            {"path": "bin/llama-server.sim", "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        ],
        "provenance": {"simulated": True},
    }
    r = admin.post(
        "/api/v1/runtime-artifacts",
        json={"recipe_id": s["recipe_id"], "receipt": receipt, "scope": "fleet", "storage": "server"},
        headers=WEB,
    )
    assert r.status_code == 201 and r.json()["verified"] == "archive_verified"
    assert (
        admin.post(
            "/api/v1/runtime-artifacts",
            json={"recipe_id": s["recipe_id"], "receipt": receipt, "scope": "fleet", "storage": "server"},
            headers=WEB,
        ).status_code
        == 409
    )
    bad = {**receipt, "files": [{"path": "../evil", "sha256": "0" * 64}]}
    assert (
        admin.post(
            "/api/v1/runtime-artifacts",
            json={"recipe_id": s["recipe_id"], "receipt": bad, "scope": "fleet", "storage": "device"},
            headers=WEB,
        ).status_code
        == 400
    )
    # traversal member in an uploaded tar is rejected
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("../escape")
        ti.size = 1
        tf.addfile(ti, io.BytesIO(b"x"))
    assert admin.post(
        "/api/v1/runtime-artifacts/upload",
        content=buf.getvalue(),
        headers={**WEB, "content-type": "application/gzip"},
    ).status_code in (400, 422)


def test_viewer_cannot_create_catalog_objects(app, admin):
    from conftest import login, make_user
    from fastapi.testclient import TestClient

    make_user(admin, "v@example.com", "viewer")
    v = login(TestClient(app), "v@example.com", "password-123")
    assert v.get("/api/v1/releases").status_code == 200
    assert (
        v.post(
            "/api/v1/eval-sets",
            json={"name": "x", "version": "1", "cases": [{"id": "a", "prompt": "p", "expected": "e"}]},
            headers=WEB,
        ).status_code
        == 403
    )
    assert v.post("/api/v1/sim/seed", headers=WEB).status_code == 403


def test_effective_budget_is_one_policy_for_the_planner_and_the_dispatched_operation(app, admin):
    """The deploy/recover payload carries the same effective budget the planner computes for that
    device and release, with per-field provenance; a device setting raises the requirement and flips
    the planner verdict for a memory value between the default and the raised requirement."""
    from convoy_server.hardware import PROFILES, effective_budget

    s = seed(admin)
    a = enrolled_agent(app, admin)
    rel = admin.get(f"/api/v1/releases/{s['release_id']}").json()
    dev = admin.get(f"/api/v1/devices/{a.device_id}").json()
    hw = PROFILES[dev["profile_id"]]
    assert hw.robot_reserve_mb == 1536 and hw.runtime_overhead_mb == 700 and hw.margin_mb == 512
    # pure function: profile defaults, then release budget, then device settings, each named
    assert effective_budget(rel["spec"], hw, {}) == {
        "runtime_overhead_mb": 700,
        "robot_reserve_mb": 1536,
        "margin_mb": 512,
        "ubatch_size": rel["spec"]["config"]["ubatch_size"],
        "kv_bytes_per_element": 2,
        "source": {"runtime_overhead_mb": "profile", "robot_reserve_mb": "profile", "margin_mb": "profile"},
    }
    with_budget = {**rel["spec"], "budget": {"runtime_overhead_mb": 800, "robot_reserve_mb": 1000}}
    e = effective_budget(with_budget, hw, {"margin_mb": 640})
    assert (e["runtime_overhead_mb"], e["robot_reserve_mb"], e["margin_mb"]) == (800, 1000, 640)
    assert e["source"] == {
        "runtime_overhead_mb": "release.budget",
        "robot_reserve_mb": "release.budget",
        "margin_mb": "device.settings",
    }
    e = effective_budget(with_budget, hw, {"robot_reserve_mb": 2000})
    assert e["robot_reserve_mb"] == 2000 and e["source"]["robot_reserve_mb"] == "device.settings"
    assert e["runtime_overhead_mb"] == 800 and e["source"]["runtime_overhead_mb"] == "release.budget"
    # planner with profile defaults: every item known, a live measurement between the two requirements
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": 20000.0})
    b0 = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert b0["unknown_items"] == [] and b0["verdict"] == "fit"
    default_required = b0["required_mb_known"]
    raised = 1536 + 1000
    between = default_required + 500.0
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": between, "disk_free_mb": 20000.0})
    assert admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()["verdict"] == "fit"
    r = admin.patch(
        f"/api/v1/devices/{a.device_id}", json={"settings": {"robot_reserve_mb": raised}}, headers=WEB
    )
    assert r.status_code == 200 and r.json()["settings"]["robot_reserve_mb"] == raised
    b1 = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert b1["verdict"] == "does_not_fit" and b1["required_mb_known"] == default_required + 1000
    reserve = next(i for i in b1["items"] if i["item"] == "robot_reserve_mb")
    assert reserve["mb"] == raised and reserve["source"].startswith("device.settings")
    assert b1["effective_budget"]["robot_reserve_mb"] == raised
    assert b1["effective_budget"]["source"]["robot_reserve_mb"] == "device.settings"
    # the deploy operation freezes exactly that policy into its payload (exact keys, ints)
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": 20000.0})
    op = admin.post(
        f"/api/v1/devices/{a.device_id}/deploy",
        json={"release_id": s["release_id"], "plan_id": s["plan_id"]},
        headers=WEB,
    )
    assert op.status_code == 201, op.text
    eb = op.json()["payload"]["effective_budget"]
    assert eb == {
        "runtime_overhead_mb": 700,
        "robot_reserve_mb": raised,
        "margin_mb": 512,
        "ubatch_size": rel["spec"]["config"]["ubatch_size"],
        "kv_bytes_per_element": 2,
        "source": {
            "runtime_overhead_mb": "profile",
            "robot_reserve_mb": "device.settings",
            "margin_mb": "profile",
        },
    }
    assert all(type(eb[k]) is int for k in eb if k != "source")
    assert eb == b1["effective_budget"]  # the budget endpoint and the payload agree
    # the device receives it in the report's operations list, and the grant binding still holds
    from helpers import deploy_success, full_deploy, grant

    rep = heartbeat(a)
    delivered = next(o for o in rep["operations"] if o["id"] == op.json()["id"])
    assert delivered["payload"]["effective_budget"] == eb
    assert delivered["payload_digest"] == op.json()["payload_digest"]
    g = grant(a, delivered)
    assert g.status_code == 200, g.text
    assert deploy_success(a, delivered, g.json()).status_code == 200
    heartbeat(a, active=s["release_id"], stage="active", generation=1)
    # a recover operation carries the policy too (same device settings, same release -> same numbers)
    full_deploy(a, admin, s["candidate_release_id"], s["candidate_plan_id"], active=s["release_id"])
    rec = admin.post(f"/api/v1/devices/{a.device_id}/restore", json={"reason": "test"}, headers=WEB)
    assert rec.status_code == 201, rec.text
    assert rec.json()["payload"]["target_release_id"] == s["release_id"]
    assert rec.json()["payload"]["effective_budget"] == eb
    # settings are explicit: the raised value stays until an operator changes it
    b2 = admin.get(f"/api/v1/releases/{s['release_id']}/budget/{a.device_id}").json()
    assert b2["effective_budget"]["robot_reserve_mb"] == raised


def test_planner_uses_the_device_strict_dimension_rules_never_an_optimistic_fit():
    """Invalid dimensional metadata yields unknown (never fit); explicit head_dim 128 and an optional
    n_vocab=None are preserved; a PRESENT invalid n_vocab is refused. Parity with convoy_agent.gguf."""
    from convoy_agent.gguf import dimension_errors
    from convoy_server.hardware import compute_buffer_mb, kv_cache_mb, plan_budget, profile

    hw = profile("jetson-orin-nano-8gb")
    live = {"mem_total_mb": 7619.0, "mem_available_mb": 6400.0, "disk_free_mb": 100000.0}

    def spec(kvi):
        return {
            "model": {"total_bytes": 1117320736, "gguf": {"kv_estimate_inputs": kvi}},
            "config": {"ctx_size": 2048, "parallel": 1, "ubatch_size": 128},
            "budget": {},
        }

    good = {
        "n_layers": 28,
        "n_kv_heads": 8,
        "head_dim": 128,
        "n_embd": 1024,
        "n_vocab": 151936,
    }  # Qwen3-0.6B: 1024/16 != 128
    assert dimension_errors(good) == {} and kv_cache_mb(good, 2048) == 2 * 28 * 8 * 128 * 2 * 2048 / (
        1024 * 1024
    )
    assert plan_budget(spec(good), hw, {}, live, observed_age_s=1.0)["verdict"] == "fit"
    for bad in (
        {**good, "n_layers": -28},
        {**good, "n_layers": 28.5},
        {**good, "n_layers": True},
        {**good, "n_kv_heads": 0},
        {**good, "head_dim": "128"},
    ):
        assert kv_cache_mb(bad, 2048) is None
        assert dimension_errors(bad), bad
        out = plan_budget(spec(bad), hw, {}, live, observed_age_s=1.0)
        assert out["verdict"] == "unknown" and "kv_cache_mb" in out["unknown_items"], bad
    # optional n_vocab: None falls back to the documented default; a present invalid value is refused
    assert compute_buffer_mb({**good, "n_vocab": None}, 128) == compute_buffer_mb(
        {**good, "n_vocab": 152064}, 128
    )
    assert (
        compute_buffer_mb({**good, "n_vocab": "x"}, 128) is None
        and compute_buffer_mb({**good, "n_embd": 0}, 128) is None
    )
    out = plan_budget(spec({**good, "n_vocab": "x"}), hw, {}, live, observed_age_s=1.0)
    assert out["verdict"] == "unknown" and "compute_buffer_mb" in out["unknown_items"]
