"""Byte-authoritative admission on the device: counts come from the sha256-verified file, a manifest
that contradicts the file is refused while the incumbent keeps serving, missing manifest metadata is
admitted from the bytes, and the cached manifest is never mutated.

Every runtime launch (candidate cutover, rollback recovery, explicit recovery operation, boot restart,
controlled restart) passes ONE gate (`Executor.launch_admission`): the pinned bytes re-read and
validated, the release's OWN effective budget, and MemAvailable read at that moment. Effective
compatibility (architecture, quantization, tokenizer, template, integer dimensions) is decided from the
parsed bytes before the grant. The budget policy mirrors the server planner and honours the per-device
overrides delivered in `payload.effective_budget`, bound per release for later retained launches."""

from __future__ import annotations

import hashlib
import json

import pytest
from convoy_agent import gguf
from convoy_agent.executor import Executor, OpFailure
from lifecycle_stub import CHATML, Stub, boot, crash, make_agent, run_op


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.stop()


def _deploy(stub, a, op_id, rid, gen, *, effective_budget=None):
    op = stub.deploy_op(op_id, rid, gen, expected_active=a.journal.get("active_release_id"))
    if effective_budget is not None:
        op["payload"]["effective_budget"] = effective_budget
    return op, run_op(a, op)


def _trace(a, mem=None):
    """Observe the order of sensor reads and runtime starts. `mem(runtime_state, n_stopped_reads)`
    may return a MemAvailable override for a read (None keeps the real simulated value)."""
    events: list[tuple[str, str | None]] = []
    real_sample, real_start = a.sensors.sample, a.sup.start
    stopped = {"n": 0}

    def sample(runtime_state=None):
        s = real_sample(runtime_state)
        if runtime_state == "stopped":
            stopped["n"] += 1
        v = mem(runtime_state, stopped["n"]) if mem else None
        if v is not None:
            s["mem_available_mb"] = float(v)
        events.append(("sample", runtime_state))
        return s

    def start(**kw):
        events.append(("start", kw["release_id"]))
        return real_start(**kw)

    a.sensors.sample = sample
    a.sup.start = start
    return events


def _starts(events):
    return [rid for kind, rid in events if kind == "start"]


def _budget(reserve: int, source: str = "device.settings") -> dict:
    return {
        "runtime_overhead_mb": 700,
        "robot_reserve_mb": reserve,
        "margin_mb": 512,
        "ubatch_size": 128,
        "kv_bytes_per_element": 2,
        "source": {"runtime_overhead_mb": "profile", "robot_reserve_mb": source, "margin_mb": "profile"},
    }


def _assert_incumbent_untouched(a, rid, gen_before, recovery=None):
    assert a.sup.state() == "running" and a.sup.generation == gen_before and a.gw.mode == "production"
    assert a.journal.get("active_release_id") == rid
    assert a.journal.get("recovery_release_id") == recovery


# ----------------------------------------------------------------------------- (3) compatibility
@pytest.mark.timeout(120)
def test_forged_manifest_counts_are_refused_at_staging_and_the_incumbent_keeps_serving(stub, tmp_path):
    stub.add_release("rel_a")
    forged = stub.add_release("rel_forged")
    forged["spec"]["model"]["gguf"]["kv_estimate_inputs"] = {"n_layers": 4, "n_kv_heads": 1, "head_dim": 64}
    forged["spec"]["model"]["gguf_provenance"] = {"method": "operator_supplied", "byte_verified": False}
    a = boot(make_agent(tmp_path, stub))
    try:
        _, out = _deploy(stub, a, "op_a", "rel_a", 1)
        assert out["status"] == "succeeded" and a.sup.state() == "running" and a.gw.mode == "production"
        gen_before = a.sup.generation
        op, out = _deploy(stub, a, "op_f", "rel_forged", 2)
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "PREFLIGHT_COMPAT" and f["stage"] == "staging"
        assert "contradict" in f["message"] and f["details"]["contradictions"]["n_layers"] == {
            "manifest": 4,
            "file": 28,
        }
        # nothing was disrupted: same runtime incarnation, still serving, active release unchanged
        _assert_incumbent_untouched(a, "rel_a", gen_before)
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_missing_manifest_metadata_is_admitted_from_the_verified_bytes_and_the_manifest_is_untouched(
    stub, tmp_path
):
    m = stub.add_release("rel_nometa")
    m["spec"]["model"]["gguf"] = {}
    m["spec"]["model"]["gguf_provenance"] = {
        "method": "unavailable",
        "byte_verified": False,
        "admission": "pending_byte_inspection",
    }
    a = boot(make_agent(tmp_path, stub))
    try:
        op, out = _deploy(stub, a, "op_n", "rel_nometa", 1)
        assert out["status"] == "succeeded", out
        row = a.journal.operation("op_n")
        staged = row["detail"]["staged"] if "staged" in (row.get("detail") or {}) else None
        adm = (staged or {}).get("admission") or {}
        if not adm:  # the terminal row keeps the last detail; look through the progress reports instead
            progress = [b for _, b in stub.posts("/outcome") if (b.get("progress") or {}).get("admission")]
            adm = progress[-1]["progress"]["admission"]
        assert adm["source"] == "verified_bytes" and adm["kv_estimate_inputs"]["n_layers"] == 28
        assert adm["items"]["kv_cache_mb"] > 0 and adm["required_mb"] > adm["items"]["weights_mb"]
        # the preflight plan said admission was pending, never a fabricated number
        pre = [b for _, b in stub.posts("/outcome") if (b.get("progress") or {}).get("preflight")]
        assert pre and pre[0]["progress"]["preflight"]["admission"] == "pending_byte_inspection"
        assert pre[0]["progress"]["preflight"]["items"]["kv_cache_mb"] is None
        # immutable manifest: the cached copy equals the server's, byte for byte, still without counts
        cached = json.loads((a.data_dir / "cache" / "manifests" / "rel_nometa.json").read_text())
        assert cached["spec"]["model"]["gguf"] == {} and cached["digest"] == m["digest"]
        assert (
            hashlib.sha256(json.dumps(cached, sort_keys=True).encode()).hexdigest()
            == hashlib.sha256(json.dumps(m, sort_keys=True).encode()).hexdigest()
        )
    finally:
        a.shutdown()


INCOMPATIBLE = [
    # (release id, GGUF header overrides, manifest gguf overrides, expected words in the message)
    ("rel_tok", {"tokenizer.ggml.model": "weird", "tokenizer.chat_template": None}, {}, ["tokenizer 'weird'", "no chat template"]),
    ("rel_arch", {"general.architecture": "weird_arch"}, {"architecture": None}, ["architecture 'weird_arch'"]),
    ("rel_quant", {"general.file_type": 99}, {}, ["quantization", "unknown(99)"]),
    ("rel_zero", {"qwen2.block_count": 0}, {"kv_estimate_inputs": None}, ["dimension n_layers", "got 0"]),
    ("rel_float", {"qwen2.block_count": 28.0}, {"kv_estimate_inputs": None}, ["dimension n_layers", "float"]),
    ("rel_bool", {"qwen2.attention.head_count_kv": True}, {"kv_estimate_inputs": None}, ["dimension n_kv_heads", "bool"]),
    ("rel_huge", {"qwen2.block_count": 100000}, {"kv_estimate_inputs": None}, ["dimension n_layers", "1..4096"]),
    ("rel_nohead", {"qwen2.attention.head_count": 7}, {"kv_estimate_inputs": None}, ["dimension head_dim", "missing"]),
]  # fmt: skip


@pytest.mark.timeout(300)
def test_incompatible_bytes_are_refused_before_the_grant_and_the_incumbent_keeps_serving(stub, tmp_path):
    """The parsed bytes decide: unsupported tokenizer without any template, unsupported architecture,
    unknown quantization, and zero / fractional / boolean / out-of-bounds / underivable dimensions are
    all PREFLIGHT_COMPAT at staging, naming the reason, with no grant requested and no disruption."""
    stub.add_release("rel_a")
    for rid, kv, manifest_gguf, _ in INCOMPATIBLE:
        m = stub.add_release(rid, gguf_kv=kv)
        for k, v in manifest_gguf.items():  # the manifest makes no claim the file could contradict
            if v is None:
                m["spec"]["model"]["gguf"].pop(k, None)
            else:
                m["spec"]["model"]["gguf"][k] = v
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        gen_before = a.sup.generation
        for i, (rid, _, _, words) in enumerate(INCOMPATIBLE, start=2):
            grants_before = len(stub.posts("/grant"))
            op, out = _deploy(stub, a, f"op_{rid}", rid, i)
            f = out["failure"]
            assert out["status"] == "failed" and f["code"] == "PREFLIGHT_COMPAT", (rid, out)
            assert f["stage"] == "staging", (rid, f)
            for w in words:
                assert w in f["message"], (rid, f["message"])
            assert f["details"]["reasons"] and not f["details"]["contradictions"], (rid, f["details"])
            assert len(stub.posts("/grant")) == grants_before  # refused before the grant
            _assert_incumbent_untouched(a, "rel_a", gen_before)
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_manifest_claims_contradicting_the_bytes_are_refused_even_when_the_claims_look_recognized(
    stub, tmp_path
):
    """A forged manifest claiming a recognized tokenizer, an embedded template and 'status: recognized'
    for a file whose bytes say otherwise is refused as a contradiction (the status is never trusted)."""
    stub.add_release("rel_a")
    m = stub.add_release(
        "rel_forged", gguf_kv={"tokenizer.ggml.model": "weird", "tokenizer.chat_template": None}
    )
    m["spec"]["model"]["gguf"].update(
        {"tokenizer_model": "gpt2", "has_chat_template": True, "file_type": "Q4_K_M", "status": "recognized"}
    )
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        gen_before = a.sup.generation
        op, out = _deploy(stub, a, "op_f", "rel_forged", 2)
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "PREFLIGHT_COMPAT" and f["stage"] == "staging"
        assert "contradict" in f["message"]
        c = f["details"]["contradictions"]
        assert c["tokenizer_model"] == {"manifest": "gpt2", "file": "weird"}
        assert c["has_chat_template"] == {"manifest": True, "file": False}
        assert "file_type" not in c  # the claim that is true is not a contradiction
        _assert_incumbent_untouched(a, "rel_a", gen_before)
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_a_reviewed_release_template_override_satisfies_the_template_rule_but_a_bad_hash_does_not(
    stub, tmp_path
):
    stub.add_release("rel_a")
    good = stub.add_release("rel_override", gguf_kv={"tokenizer.chat_template": None})
    good["spec"]["template"] = {"text": CHATML, "sha256": hashlib.sha256(CHATML.encode()).hexdigest()}
    bad = stub.add_release("rel_badhash", gguf_kv={"tokenizer.chat_template": None})
    bad["spec"]["template"] = {"text": CHATML, "sha256": "0" * 64}
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        gen_before = a.sup.generation
        _, out = _deploy(stub, a, "op_bad", "rel_badhash", 2)
        assert out["status"] == "failed" and out["failure"]["code"] == "DIGEST_MISMATCH", out
        _assert_incumbent_untouched(a, "rel_a", gen_before)
        _, out = _deploy(stub, a, "op_good", "rel_override", 3)
        assert out["status"] == "succeeded", out
        assert out["evidence"]["admission"]["compat"]["template"] == "release_override"
        assert a.journal.get("active_release_id") == "rel_override" and a.sup.state() == "running"
    finally:
        a.shutdown()


def test_kv_sizing_rejects_zero_negative_fractional_boolean_and_non_numeric_counts():
    cfg = {"ctx_size": 2048, "parallel": 1}
    assert Executor._kv_mb({"n_layers": 28, "n_kv_heads": 2, "head_dim": 128}, cfg) == 56.0
    for bad in (0, -1, 28.0, 27.5, True, "28", None, 5000):
        assert Executor._kv_mb({"n_layers": bad, "n_kv_heads": 2, "head_dim": 128}, cfg) is None, bad
    assert Executor._kv_mb({"n_layers": 28, "n_kv_heads": 2}, cfg) is None
    assert gguf.dimension_errors({"n_layers": 28, "n_kv_heads": 2, "head_dim": 128, "n_embd": 0}) == {
        "n_embd": "must be within 1..65536, got 0"
    }


# ----------------------------------------------------------------------------- (2) every launch is gated
@pytest.mark.timeout(120)
def test_insufficient_memory_after_the_incumbent_stops_never_starts_the_candidate_and_recovers(
    stub, tmp_path
):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        _, out = _deploy(stub, a, "op_a", "rel_a", 1)
        assert out["status"] == "succeeded"
        # while the incumbent runs the pool looks fine; once it has stopped the fresh read the candidate
        # gets shows that something else took the memory (e.g. the robot stack): cutover must fail. The
        # retained release is judged by ITS OWN fresh read, which shows the pressure is gone.
        events = _trace(a, mem=lambda state, n: 100.0 if state == "stopped" and n == 1 else None)
        op, out = _deploy(stub, a, "op_b", "rel_b", 2)
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "CUTOVER_MEMORY", out
        assert f["details"]["mem_available_mb"] == 100.0 and f["details"]["required_mb"] > 100
        assert f["details"]["candidate_started"] is False
        # the candidate was never started; the incumbent was restored (rollback to the recovery release)
        assert _starts(events) == ["rel_a"]
        assert a.journal.get("active_release_id") == "rel_a" and a.sup.state() == "running"
        rec = f["details"]["recovery"]
        assert rec["recovered"] is True and rec["launch_admission"]["release_id"] == "rel_a"
        assert rec["launch_admission"]["mem_available_mb"] > rec["launch_admission"]["required_mb"]
        # the retained release had its own fresh read, after the stop and before its start
        i_start = events.index(("start", "rel_a"))
        stopped_reads = [i for i, e in enumerate(events) if e == ("sample", "stopped")]
        assert len(stopped_reads) >= 2 and stopped_reads[-1] < i_start
        assert a.gw.mode == "production" and a.journal.get("health") == "ok"
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_when_nothing_fits_after_the_stop_the_device_degrades_explicitly_without_starting_anything(
    stub, tmp_path
):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        events = _trace(a, mem=lambda state, n: 100.0 if state == "stopped" else None)
        op, out = _deploy(stub, a, "op_b", "rel_b", 2)
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "CUTOVER_MEMORY", out
        rec = f["details"]["recovery"]
        assert rec["recovered"] is False and rec["degraded"] is True
        assert rec["last"]["code"] == "RECOVERY_MEMORY" and "rel_a" in rec["last"]["message"]
        assert (
            rec["last"]["details"]["mem_available_mb"] == 100.0
            and rec["last"]["details"]["required_mb"] > 100
        )
        assert _starts(events) == []  # neither the candidate nor the retained release was started
        assert ("sample", "stopped") in events
        assert a.sup.state() == "stopped" and a.journal.get("active_release_id") is None
        assert a.journal.get("health") == "failed" and a.gw.mode == "closed"
        assert a.journal.operation("op_b")["stage"] == "Degraded"
        assert a.journal.get("failed_generation_latch") == 2
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_explicit_recovery_operation_is_gated_before_the_grant_and_again_after_the_stop(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        assert _deploy(stub, a, "op_b", "rel_b", 2)[1]["status"] == "succeeded"
        assert a.journal.get("recovery_release_id") == "rel_a"
        gen_before = a.sup.generation
        # (i) the retained manifest now contradicts the retained bytes: refused before the grant
        cached = a.data_dir / "cache" / "manifests" / "rel_a.json"
        m = json.loads(cached.read_text())
        m["spec"]["model"]["gguf"]["kv_estimate_inputs"] = {"n_layers": 4, "n_kv_heads": 1, "head_dim": 64}
        cached.write_text(json.dumps(m))
        grants_before = len(stub.posts("/grant"))
        op = stub.recover_op("op_r1", "rel_a", 3)
        op["payload"]["expected_active_release_id"] = "rel_b"
        out = run_op(a, op)
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "RECOVERY_METADATA" and f["stage"] == "staging", out
        assert "contradict" in f["message"] and f["details"]["disruption_started"] is False
        assert len(stub.posts("/grant")) == grants_before
        _assert_incumbent_untouched(a, "rel_b", gen_before, recovery="rel_a")
        cached.write_text(json.dumps(stub.manifests["rel_a"]))
        # (ii) memory: the provisional read (incumbent serving) passes, the fresh read after the stop
        # does not: no child is started, the device is explicitly degraded
        events = _trace(a, mem=lambda state, n: 100.0 if state == "stopped" else None)
        op = stub.recover_op("op_r2", "rel_a", 4)
        op["payload"]["expected_active_release_id"] = "rel_b"
        out = run_op(a, op)
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "RECOVERY_MEMORY" and f["stage"] == "cutover", out
        assert f["details"]["mem_available_mb"] == 100.0 and f["details"]["runtime_started"] is False
        assert _starts(events) == [] and ("sample", "stopped") in events
        assert a.sup.state() == "stopped" and a.journal.get("active_release_id") is None
        assert a.journal.get("health") == "failed" and a.gw.mode == "closed"
        assert a.journal.operation("op_r2")["stage"] == "Degraded"
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_boot_refuses_a_retained_release_on_insufficient_fresh_memory_or_invalid_metadata(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
    assert _deploy(stub, a, "op_b", "rel_b", 2)[1]["status"] == "succeeded"
    crash(a)
    # (i) invalid metadata: the pinned active file no longer parses; rel_b is not started, the retained
    # recovery release rel_a still qualifies and fits, so the device recovers to it
    sha_b = stub.manifests["rel_b"]["spec"]["model"]["file"]["sha256"]
    model_b = a.data_dir / "cache" / "models" / f"{sha_b}.gguf"
    intact = model_b.read_bytes()
    model_b.write_bytes(b"not a gguf at all" + b"\0" * 4096)
    b = make_agent(tmp_path, stub)
    events = _trace(b)
    b.acquire_lock()
    b.start_local()
    try:
        note = b._restart_note
        assert note["action"] == "recovered_on_start" and note["result"]["recovered"] is True
        assert _starts(events) == ["rel_a"]
        assert b.sup.state() == "running" and b.journal.get("active_release_id") == "rel_a"
        assert b.gw.mode == "production" and b.journal.get("health") == "ok"
        row = b.journal.operation(note["result"]["operation_id"])
        assert row["outcome"]["failure"]["code"] == "RECOVERY_METADATA"
        # boot re-hashes the whole pinned file first, so the corruption is caught as a sha256 mismatch
        assert "does not match its sha256" in row["outcome"]["failure"]["message"]
        assert note["result"]["launch_admission"]["release_id"] == "rel_a"
        # the retained release was gated by a fresh read before its start
        assert events.index(("sample", "stopped")) < events.index(("start", "rel_a"))
    finally:
        crash(b)
    model_b.write_bytes(intact)
    # (ii) insufficient fresh memory for everything retained: explicit degraded state, nothing started
    c = make_agent(tmp_path, stub)
    events = _trace(c, mem=lambda state, n: 100.0)
    c.acquire_lock()
    c.start_local()
    try:
        note = c._restart_note
        assert note["action"] == "recovered_on_start" and note["result"]["recovered"] is False
        assert note["result"]["degraded"] is True and note["result"]["last"]["code"] == "RECOVERY_MEMORY"
        assert _starts(events) == [] and ("sample", "stopped") in events
        assert c.sup.state() != "running" and c.journal.get("active_release_id") is None
        assert c.journal.get("health") == "failed" and c.gw.mode == "closed"
        row = c.journal.operation(note["result"]["operation_id"])
        assert row["outcome"]["failure"]["code"] == "RECOVERY_MEMORY"
        assert row["outcome"]["failure"]["details"]["mem_available_mb"] == 100.0
    finally:
        c.shutdown()


@pytest.mark.timeout(120)
def test_controlled_restart_refuses_on_insufficient_fresh_memory_or_invalid_metadata(stub, tmp_path):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        gen_before = a.sup.generation
        events = _trace(a, mem=lambda state, n: 100.0 if state == "stopped" else None)
        a.gw.needs_restart = True
        a._restart_runtime_controlled()
        assert _starts(events) == [] and ("sample", "stopped") in events
        assert a.sup.generation == gen_before and a.sup.state() == "stopped"
        assert a.journal.get("health") == "failed" and a.gw.mode == "closed"
        assert a.journal.get("active_release_id") is None
        note = a._restart_note
        assert note["action"] == "recovered_after_restart_failure" and note["result"]["recovered"] is False
        row = a.journal.operation(note["result"]["operation_id"])
        assert row["outcome"]["failure"]["code"] == "RECOVERY_MEMORY"
        assert row["outcome"]["failure"]["details"]["mem_available_mb"] == 100.0
    finally:
        a.shutdown()
    # invalid metadata on a fresh fixture: the pinned file is corrupt but still the active pointer
    stub2 = stub
    d = boot(make_agent(tmp_path, stub2, name="dev2"))
    try:
        assert _deploy(stub2, d, "op_a2", "rel_a", 1)[1]["status"] == "succeeded"
        sha = stub2.manifests["rel_a"]["spec"]["model"]["file"]["sha256"]
        (d.data_dir / "cache" / "models" / f"{sha}.gguf").write_bytes(b"GGUF" + b"\xff" * 64)
        gen_before = d.sup.generation
        events = _trace(d)
        d.gw.needs_restart = True
        d._restart_runtime_controlled()
        assert _starts(events) == [] and d.sup.generation == gen_before and d.sup.state() == "stopped"
        assert d.journal.get("health") == "failed" and d.gw.mode == "closed"
        row = d.journal.operation(d._restart_note["result"]["operation_id"])
        assert row["outcome"]["failure"]["code"] == "RECOVERY_METADATA"
    finally:
        d.shutdown()


# ----------------------------------------------------------------------------- (4) one budget policy
def test_required_mb_mirrors_the_server_planner_items():
    class J:
        def get(self, k, default=None):
            return default

    ex = Executor.__new__(Executor)
    ex.j = J()
    spec = {"model": {"total_bytes": 1000 * 1024 * 1024}, "config": {"ctx_size": 2048, "parallel": 1, "ubatch_size": 128}, "budget": {}}  # fmt: skip
    kvi = {"n_layers": 28, "n_kv_heads": 2, "head_dim": 128, "n_embd": 1536, "n_vocab": 151936}
    budget = ex.effective_budget(spec)
    assert budget == {
        "runtime_overhead_mb": 700, "robot_reserve_mb": 1536, "margin_mb": 512, "ubatch_size": 128, "kv_bytes_per_element": 2,
        "source": {"runtime_overhead_mb": "profile", "robot_reserve_mb": "profile", "margin_mb": "profile"},
        "resolved": {"runtime_overhead_mb": "profile", "robot_reserve_mb": "profile", "margin_mb": "profile"},
    }  # fmt: skip
    req = ex.required_mb(spec, kvi, budget)
    kv = 2 * 28 * 2 * 128 * 2 * 2048 / (1024 * 1024)
    comp = (151936 * 128 * 4 + 1536 * 128 * 4 * 16) / (1024 * 1024)
    assert req["items"]["kv_cache_mb"] == round(kv, 1) and req["items"]["compute_buffer_mb"] == round(comp, 1)
    assert req["required_mb"] == round(1000 + kv + comp + 700 + 1536 + 512, 1)
    # payload overrides win over the release budget, the release budget over the profile
    spec["budget"] = {"margin_mb": 256}
    b2 = ex.effective_budget(spec, _budget(3000))
    assert b2["robot_reserve_mb"] == 3000 and b2["margin_mb"] == 512  # the payload carried margin 512
    assert b2["source"]["robot_reserve_mb"] == "device.settings"
    assert b2["resolved"]["robot_reserve_mb"] == "operation.payload"
    b3 = ex.effective_budget(spec)
    assert b3["margin_mb"] == 256 and b3["source"]["margin_mb"] == "release.budget"
    assert b3["resolved"]["margin_mb"] == "release.budget" and b3["resolved"]["robot_reserve_mb"] == "profile"


@pytest.mark.timeout(180)
def test_device_reserve_from_the_payload_is_enforced_at_admission_and_bound_for_retained_launches(
    stub, tmp_path
):
    stub.add_release("rel_a")
    stub.add_release("rel_b")
    a = boot(make_agent(tmp_path, stub))
    spec_b = stub.manifests["rel_b"]["spec"]
    kvi = {"n_layers": 28, "n_kv_heads": 2, "head_dim": 128, "n_embd": 1536, "n_vocab": 151936}
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        gen_before = a.sup.generation
        old_required = a.exec.required_mb(spec_b, kvi, a.exec.effective_budget(spec_b))["required_mb"]
        new_required = a.exec.required_mb(spec_b, kvi, a.exec.effective_budget(spec_b, _budget(4000)))[
            "required_mb"
        ]
        avail = a.sensors.sample(a.sup.state())["mem_available_mb"]
        assert old_required < avail < new_required  # fits under the defaults, not under the device reserve
        _, out = _deploy(stub, a, "op_b1", "rel_b", 2, effective_budget=_budget(4000))
        f = out["failure"]
        assert out["status"] == "failed" and f["code"] == "PREFLIGHT_MEMORY" and f["stage"] == "admission", (
            out
        )
        assert (
            f["details"]["required_mb"] == new_required and f["details"]["budget"]["robot_reserve_mb"] == 4000
        )
        assert f["details"]["budget"]["source"]["robot_reserve_mb"] == "device.settings"
        assert f["details"]["budget"]["resolved"]["robot_reserve_mb"] == "operation.payload"
        assert f["details"]["items"]["compute_buffer_mb"] > 0
        _assert_incumbent_untouched(a, "rel_a", gen_before)
        # a reserve the release does fit under: admitted, and BOUND to rel_b for later retained launches
        _, out = _deploy(stub, a, "op_b2", "rel_b", 3, effective_budget=_budget(3000))
        assert out["status"] == "succeeded", out
        bound = a.journal.get("effective_budget:rel_b")
        assert bound["robot_reserve_mb"] == 3000 and bound["source"]["robot_reserve_mb"] == "device.settings"
        assert out["evidence"]["admission"]["budget"]["robot_reserve_mb"] == 3000
        bound_required = out["evidence"]["admission"]["required_mb"]
        assert (
            bound_required
            == a.exec.required_mb(spec_b, kvi, a.exec.effective_budget(spec_b, _budget(3000)))["required_mb"]
        )
    finally:
        crash(a)
    # boot with no operation payload anywhere: rel_b is judged under its BOUND reserve (3000) and refused
    # at a MemAvailable that would have admitted it under the defaults; rel_a (bound under the defaults)
    # fits and is recovered
    mid = (old_required + bound_required) / 2
    b = make_agent(tmp_path, stub)
    events = _trace(b, mem=lambda state, n: mid)
    b.acquire_lock()
    b.start_local()
    try:
        note = b._restart_note
        assert note["action"] == "recovered_on_start" and note["result"]["recovered"] is True, note
        assert _starts(events) == ["rel_a"]
        assert b.sup.state() == "running" and b.journal.get("active_release_id") == "rel_a"
        row = b.journal.operation(note["result"]["operation_id"])
        failure = row["outcome"]["failure"]
        assert failure["code"] == "RECOVERY_MEMORY" and failure["details"]["required_mb"] == bound_required
        assert failure["details"]["budget"]["robot_reserve_mb"] == 3000
        assert failure["details"]["budget"]["source"]["robot_reserve_mb"] == "device.settings"
        assert failure["details"]["budget"]["resolved"]["robot_reserve_mb"] == "journal.bound"
        assert note["result"]["launch_admission"]["required_mb"] == old_required
        assert note["result"]["launch_admission"]["budget"]["robot_reserve_mb"] == 1536
    finally:
        b.shutdown()


def test_launch_admission_raises_op_failure_with_the_named_codes(stub, tmp_path):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    spec = stub.manifests["rel_a"]["spec"]
    try:
        with pytest.raises(OpFailure) as ei:  # nothing staged yet
            a.exec.launch_admission(spec, "rel_a", a.exec.effective_budget(spec), stage="restart")
        assert ei.value.code == "RECOVERY_METADATA" and "missing" in str(ei.value)
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_same_size_valid_header_corruption_is_refused_at_boot_and_controlled_restart_but_intact_bytes_boot(
    stub, tmp_path
):
    """A pinned file whose header still parses but whose trailing tensor/padding byte changed (same size,
    same header) must not be launched at boot or by a controlled restart: both re-hash the whole file.
    A refused launch degrades the retained pointer, so each refusal gets its own fixture."""
    stub.add_release("rel_a")

    def corrupted(agent):
        sha = stub.manifests["rel_a"]["spec"]["model"]["file"]["sha256"]
        model = agent.data_dir / "cache" / "models" / f"{sha}.gguf"
        intact = model.read_bytes()
        corrupt = intact[:-1] + bytes([intact[-1] ^ 0x01])  # last (padding) byte flipped, size unchanged
        assert len(corrupt) == len(intact) and corrupt != intact
        return model, corrupt

    # fixture 1: intact boot is healthy; the same file with one trailing byte changed is refused at boot
    a = boot(make_agent(tmp_path, stub, name="dev-boot"))
    assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
    model, corrupt = corrupted(a)
    crash(a)
    b = make_agent(tmp_path, stub, name="dev-boot")
    b.acquire_lock()
    b.start_local()
    assert b.sup.state() == "running" and b.journal.get("active_release_id") == "rel_a"
    assert b.gw.mode == "production" and b.journal.get("health") == "ok"
    assert (b.journal.get("launch_admission") or {}).get("sha256_verified") is True
    crash(b)
    model.write_bytes(corrupt)
    c = make_agent(tmp_path, stub, name="dev-boot")
    events = _trace(c)
    c.acquire_lock()
    c.start_local()
    try:
        assert _starts(events) == [] and c.sup.state() != "running"
        assert c.journal.get("health") == "failed" and c.gw.mode == "closed"
        note = c._restart_note
        assert note["result"]["recovered"] is False
        row = c.journal.operation(note["result"]["operation_id"])
        assert (
            row["outcome"]["failure"]["code"] == "RECOVERY_METADATA"
            and "sha256" in row["outcome"]["failure"]["message"]
        )
    finally:
        c.shutdown()
    # fixture 2: a running device whose pinned file is corrupted the same way refuses a controlled restart
    d = boot(make_agent(tmp_path, stub, name="dev-restart"))
    try:
        assert _deploy(stub, d, "op_d", "rel_a", 1)[1]["status"] == "succeeded"
        model_d, corrupt_d = corrupted(d)
        gen_before = d.sup.generation
        model_d.write_bytes(corrupt_d)
        events = _trace(d)
        d._restart_runtime_controlled()
        assert d.sup.state() != "running" and d.sup.generation == gen_before and d.gw.mode == "closed"
        assert d.journal.get("health") == "failed" and _starts(events) == []
        la = d.journal.get("launch_admission") or {}
        assert (
            la.get("refused") is True
            and la.get("code") == "RECOVERY_METADATA"
            and "sha256" in la.get("message", "")
        )
    finally:
        d.shutdown()


@pytest.mark.timeout(120)
def test_present_but_invalid_kv_head_count_is_refused_not_replaced_by_the_attention_head_count(
    stub, tmp_path
):
    stub.add_release("rel_a")
    stub.add_release("rel_kv0", gguf_kv={"qwen2.attention.head_count_kv": 0})
    stub.add_release("rel_kvfalse", gguf_kv={"qwen2.attention.head_count_kv": False})
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        gen_before = a.sup.generation
        for n, rid in enumerate(("rel_kv0", "rel_kvfalse"), start=2):
            # the manifest advertises the fixture's usual counts; the BYTES carry the invalid KV head count
            _, out = _deploy(stub, a, f"op_{rid}", rid, n)
            f = out["failure"]
            assert (
                out["status"] == "failed"
                and f["code"] == "PREFLIGHT_COMPAT"
                and f["stage"] in ("staging", "admission")
            ), out
            assert "n_kv_heads" in json.dumps(
                f
            )  # refused for the KV head dimension, not substituted by head_count=12
            assert a.sup.state() == "running" and a.sup.generation == gen_before and a.gw.mode == "production"
            assert a.journal.get("active_release_id") == "rel_a"
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_missing_embedding_size_makes_the_requirement_unknown_and_is_refused_by_name(stub, tmp_path):
    stub.add_release("rel_a")
    stub.add_release(
        "rel_noembd", gguf_kv={"qwen2.embedding_length": None, "qwen2.attention.key_length": 128}
    )
    a = boot(make_agent(tmp_path, stub))
    try:
        assert _deploy(stub, a, "op_a", "rel_a", 1)[1]["status"] == "succeeded"
        # unit: KV sizable, compute buffer not -> required unknown, the unknown term is named
        req = a.exec.required_mb(
            stub.manifests["rel_a"]["spec"],
            {"n_layers": 28, "n_kv_heads": 2, "head_dim": 128},
            a.exec.effective_budget(stub.manifests["rel_a"]["spec"], None, "rel_a"),
        )
        assert (
            req["required_mb"] is None
            and req["unknown_terms"] == ["compute_buffer_mb"]
            and req["items"]["kv_cache_mb"] > 0
        )
        gen_before = a.sup.generation
        _, out = _deploy(stub, a, "op_n", "rel_noembd", 2)
        f = out["failure"]
        assert out["status"] == "failed" and "compute_buffer_mb" in json.dumps(f), out
        assert (
            a.sup.state() == "running"
            and a.sup.generation == gen_before
            and a.journal.get("active_release_id") == "rel_a"
        )
    finally:
        a.shutdown()
