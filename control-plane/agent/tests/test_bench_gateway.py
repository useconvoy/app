"""The hardware benchmark script: honest derivations (TTFT/TPOT unavailable stays unavailable, sample
sizes on every percentile), strict fixture scoring, per-cell aggregation, persisted records with typed
transport failures and partial reports, complete raw responses with both trace ids, honest trace
evidence accounting, a byte-exact render check, and end-to-end runs through a REAL gateway (the
simulated runtime behind the lifecycle stub) with the report written to disk."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import socket
import threading
import time
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from lifecycle_stub import Stub, boot, make_agent, run_op

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "jetson" / "bench_gateway.py"
TEMPLATE = REPO / "docs" / "templates" / "qwen3-nonthinking.jinja"
spec = importlib.util.spec_from_file_location("bench_gateway", SCRIPT)
bench = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(bench)


@pytest.fixture()
def stub():
    s = Stub()
    yield s
    s.stop()


# --------------------------------------------------------------------------- stand-ins
class FakeGateway:
    """Loopback HTTP stand-in for the gateway with scripted behaviour per request: a well-formed 200
    (with configurable header/body trace ids and body padding), malformed JSON, a hang, or a 503."""

    def __init__(self):
        self.mode = "ok"
        self.header_trace: str | None = "tr_hdr"
        self.body_trace: str | None = "tr_hdr"
        self.pad_bytes = 0
        self.hang_s = 0.0
        self.requests: list[dict[str, Any]] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code: int, raw: bytes, trace: str | None):
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                if trace is not None:
                    self.send_header("X-Convoy-Trace-Id", trace)
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                srv.requests.append(body)
                if srv.mode == "hang":
                    time.sleep(srv.hang_s)
                if srv.mode == "malformed":
                    return self._send(200, b"{not json", srv.header_trace)
                if srv.mode == "http_503":
                    return self._send(
                        503,
                        json.dumps(
                            {"error": {"message": "gateway in eval mode", "type": "unavailable"}}
                        ).encode(),
                        srv.header_trace,
                    )
                obj = {
                    "id": "chatcmpl-x",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "Paris"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 1, "total_tokens": 31},
                    "convoy": {
                        "trace_id": srv.body_trace, "release_id": "rel_x", "queue_ms": 0.1, "latency_ms": 12.5, "ttft_ms": None,
                        "timings": {"prompt_ms": 3.0, "predicted_ms": 8.0, "predicted_n": 1, "prompt_n": 30, "predicted_per_second": 125.0},
                        "simulated": True,
                    },
                }  # fmt: skip
                if srv.pad_bytes:
                    obj["filler"] = "x" * srv.pad_bytes
                return self._send(200, json.dumps(obj).encode(), srv.header_trace)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class FakeRuntime:
    """Loopback stand-in for llama-server's /apply-template and /tokenize (Bearer auth), scripted:
    pass | wrong_suffix | empty_tokens | bad_tokens."""

    def __init__(self, key: str):
        self.mode = "pass"
        self.key = key
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _json(self, code: int, obj: Any):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if self.headers.get("Authorization") != f"Bearer {srv.key}":
                    return self._json(401, {"error": "unauthorized"})
                if self.path == "/apply-template":
                    # independent literal rendering (not the script's renderer)
                    turns = "".join(
                        f"<|im_start|>{m['role']}\n{m['content']}<|im_end|>\n" for m in body["messages"]
                    )
                    tail = (
                        "<|im_start|>assistant\n"
                        if srv.mode == "wrong_suffix"
                        else "<|im_start|>assistant\n<think>\n\n</think>\n\n"
                    )
                    return self._json(200, {"prompt": turns + tail})
                if self.path == "/tokenize":
                    if srv.mode == "empty_tokens":
                        return self._json(200, {"tokens": []})
                    if srv.mode == "bad_tokens":
                        return self._json(200, {"tokens": [1, "x", True]})
                    return self._json(200, {"tokens": [151644, 8948, 198, 2610, 151645]})
                return self._json(404, {"error": "nope"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class FakeApi:
    """Control-plane stand-in: tr_ok is present, tr_lag appears after two empty answers (spool lag),
    tr_empty never has spans, tr_err always fails."""

    def __init__(self, lag: int = 2):
        self.calls: list[str] = []
        self.lag = lag

    def get(self, path: str) -> Any:
        self.calls.append(path)
        if path == "/api/v1/devices/dev_1":
            return {"id": "dev_1", "name": "dev"}
        if path.startswith("/api/v1/devices/dev_1/telemetry"):
            return [{"ts": 1}, {"ts": 2}, {"ts": 3}]
        tid = path.rsplit("/", 1)[1]
        if tid == "tr_err":
            raise urllib.error.HTTPError(path, 404, "not found", {}, None)  # type: ignore[arg-type]
        if tid == "tr_empty":
            return {"trace_id": tid, "spans": [], "logs": [], "operation": None}
        if tid == "tr_lag" and self.lag > 0:
            self.lag -= 1
            return {"trace_id": tid, "spans": [], "logs": [], "operation": None}
        return {"trace_id": tid, "spans": [{"name": "gateway.chat_completion", "status": "ok"}], "logs": []}


def _closed_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _args(**over: Any) -> list[str]:
    base = {"--out": None, "--gateway": None, "--repeats": "2", "--warmup": "1", "--label": "test"}
    base.update(over)
    out: list[str] = []
    for k, v in base.items():
        if v is None:
            continue
        if v is True:
            out.append(k)
        elif isinstance(v, list):
            for x in v:
                out += [k, str(x)]
        else:
            out += [k, str(v)]
    return out


# --------------------------------------------------------------------------- derivations and scoring
def test_percentiles_carry_sample_sizes_and_unknowns_are_not_zero():
    s = bench.summarize([10.0, 20.0, None, 30.0])
    assert (
        s["n"] == 3
        and s["unavailable"] == 1
        and s["p50"] == 20.0
        and s["p95"] == 30.0
        and "indicative" in s["note"]
    )
    assert bench.summarize([None, None]) == {"n": 0, "unavailable": 2, "note": "no samples"}
    big = bench.summarize([float(i) for i in range(1, 101)])
    assert big["p50"] == 50.0 and big["p95"] == 95.0 and "note" not in big
    assert bench.percentile([], 50) is None


def test_strict_scoring_rejects_substrings_prose_nulls_and_wrong_values():
    label = {"scoring": "label", "expected": "UNSAFE"}
    assert bench.score_case(" unsafe.\n", label) is True
    assert bench.score_case("UNSAFE", label) is True
    assert bench.score_case("SAFE", label) is False
    assert bench.score_case("It is UNSAFE", label) is False  # no substring credit
    exact = {"scoring": "exact", "expected": "2"}
    assert bench.score_case("2", exact) is True and bench.score_case("2.", exact) is True
    assert bench.score_case("12", exact) is False  # "12" never satisfies "2"
    assert bench.score_case("Two", exact) is False
    assert bench.score_case("'Paris'", {"scoring": "exact", "expected": "Paris"}) is True
    js = {"scoring": "json_exact", "expected": {"action": "goto", "target": "dock_1"}}
    assert bench.score_case('{"action": "goto", "target": "dock_1"}', js) is True
    assert bench.score_case('{"target": "dock_1", "action": "goto"}', js) is True  # key order irrelevant
    assert bench.score_case('```json\n{"action": "goto", "target": "dock_1"}\n```', js) is True
    assert bench.score_case('{"action": "goto", "target": null}', js) is False  # null fails
    assert bench.score_case('{"action": "goto", "target": "dock_2"}', js) is False  # wrong value fails
    assert bench.score_case('{"action": "goto"}', js) is False  # missing key fails
    assert bench.score_case('{"action": "goto", "target": "dock_1", "extra": 1}', js) is False
    assert bench.score_case('Sure! {"action": "goto", "target": "dock_1"}', js) is False  # prose fails
    assert bench.score_case("[1, 2]", js) is False and bench.score_case("", js) is False
    assert bench.score_case(
        '{"ok": true, "code": 0}', {"scoring": "json_exact", "expected": {"ok": True, "code": 0}}
    )
    assert bench.score_case(
        '{"ok": 1, "code": 0}', {"scoring": "json_exact", "expected": {"ok": True, "code": 0}}
    )
    # (1 == True in Python: documented as a known limitation of json_exact for boolean fields)
    assert bench.score_case("anything", {"kind": "custom"}) is None  # no rule, no expectation


def test_builtin_cases_stay_loose_diagnostics_in_short_and_long_variants():
    assert bench.check_expectation(" UNSAFE.\n", {"one_of": ["unsafe"]}) is True
    assert bench.check_expectation("safe", {"one_of": ["UNSAFE"]}) is False
    assert (
        bench.check_expectation(
            'Sure: {"action": "pick", "target": "cup"}', {"json_keys": ["action", "target"]}
        )
        is True
    )
    assert bench.check_expectation("{not json", {"json_keys": ["a"]}) is False
    assert bench.check_expectation("The capital is Paris.", {"contains": ["paris"]}) is True
    assert bench.check_expectation("anything", None) is None
    cases, fixture = bench.load_fixture("builtin")
    assert len(cases) == 2 * len(bench.BUILTIN_CASES)
    assert {c["length"] for c in cases} == {"short", "long"}
    assert all(c["fixture"] == "builtin-diagnostic" for c in cases)
    assert fixture["strict"] is False and "diagnostic, not the strict fixture" in fixture["note"]
    long = [c for c in cases if c["length"] == "long"][0]
    assert len(long["prompt"]) > 4000 and long["prompt"].endswith(bench.BUILTIN_CASES[0]["prompt"])


# --------------------------------------------------------------------------- fixtures
def test_fixture_v1_is_frozen_strict_and_smoke_is_a_verbatim_subset():
    cases, fx = bench.load_fixture("v1")
    assert 24 <= len(cases) <= 40 and fx["strict"] is True and fx["frozen"] is True
    assert fx["protocol_version"] == "convoy-bench-2" and "NOT customer acceptance" in fx["note"]
    assert fx["sha256"] == hashlib.sha256(Path(fx["path"]).read_bytes()).hexdigest()
    assert len({c["id"] for c in cases}) == len(cases)
    for c in cases:
        assert c["kind"] in ("label", "json", "semantic") and c["system"] and c["prompt"]
        assert c["scoring"] == {"label": "label", "json": "json_exact", "semantic": "exact"}[c["kind"]]
        assert c["scoring"] != "json_exact" or isinstance(c["expected"], dict)
        assert 1 <= c["max_tokens"] <= 128
    assert {c["kind"] for c in cases} == {"label", "json", "semantic"}
    smoke, sfx = bench.load_fixture("smoke")
    by_id = {c["id"]: c for c in cases}
    assert len(smoke) == 8 and sfx["strict"] and sfx["frozen"]
    for s in smoke:
        strict = {k: v for k, v in by_id[s["id"]].items() if k != "fixture"}
        assert {k: v for k, v in s.items() if k != "fixture"} == strict
    with pytest.raises(ValueError):
        bench.validate_cases([{"id": "a", "prompt": "p", "scoring": "contains", "expected": "x"}])
    with pytest.raises(ValueError):
        bench.validate_cases([{"id": "a", "prompt": "p"}, {"id": "a", "prompt": "q"}])


def test_matrix_plan_is_27_requests_per_probe_across_9_cells_with_deterministic_filler():
    cases, _ = bench.load_fixture("v1")
    probe = [c for c in cases if c["id"] == "sem-capital-france"][0]
    cells = bench.build_matrix([probe])
    assert len(cells) == 9 and {c["input_band"] for c in cells} == {64, 256, 1024}
    assert {c["output_cap"] for c in cells} == {16, 64, 128}
    assert all(c["max_tokens"] == c["output_cap"] and c["probe_id"] == "sem-capital-france" for c in cells)
    big = [c for c in cells if c["input_band"] == 1024][0]
    small = [c for c in cells if c["input_band"] == 64][0]
    assert big["pad_chars"] > small["pad_chars"] >= 0 and big["prompt"].endswith(probe["prompt"])
    assert 3500 <= big["pad_chars"] <= 4100  # ~1024 tokens at the 4 chars/token heuristic, minus the base
    assert bench.build_matrix([probe]) == cells  # deterministic
    import argparse

    ns = argparse.Namespace(matrix=True, probe=["sem-capital-france"], bands="64,256,1024", caps="16,64,128",
                            chars_per_token=4.0, warmup=1, matrix_repeats=3, repeats=5)  # fmt: skip
    plan = bench._plan(ns, cases)
    assert sum(1 for p in plan if p[0] == "matrix") == 27 and sum(1 for p in plan if p[0] == "warmup") == 9
    ns.probe = ["does-not-exist"]
    with pytest.raises(ValueError):
        bench._plan(ns, cases)


# --------------------------------------------------------------------------- B5 aggregation per cell
def test_aggregate_is_per_cell_and_labels_pooled_mixtures():
    def rec(case_id, kind, band, cap, lat, ok=True, met=True, **extra):
        r = {"case_id": case_id, "kind": kind, "input_band": band, "output_cap": cap, "ok": ok,
             "gateway_latency_ms": lat, "ttft_ms": None, "tpot_ms": 1.0 if ok else None, "expectation_met": met,
             "scoring": "exact", "evidence_defects": []}  # fmt: skip
        r.update(extra)
        return r

    rows = [
        rec("p", "semantic", 64, 16, 100),
        rec("p", "semantic", 64, 16, 110),
        rec("p", "semantic", 64, 128, 900),
        rec("p", "semantic", 1024, 16, 500, met=False),
        rec(
            "q",
            "json",
            "unpadded",
            64,
            50,
            ok=False,
            met=None,
            error_type="unsupported_feature",
            tpot_ms=None,
        ),
    ]
    agg = bench.aggregate(rows)
    assert set(agg["cells"]) == {
        "p/band=64/cap=16",
        "p/band=64/cap=128",
        "p/band=1024/cap=16",
        "q/band=unpadded/cap=64",
    }
    c = agg["cells"]["p/band=64/cap=16"]
    assert c["n"] == 2 and c["metrics"]["gateway_latency_ms"]["p50"] == 100 and "pooled" not in c
    assert c["input_band"] == 64 and c["output_cap"] == 16 and c["expectation"] == {
        "checked": 2, "met": 2, "rules": ["exact"], "note": c["expectation"]["note"]}  # fmt: skip
    assert agg["cells"]["p/band=64/cap=128"]["metrics"]["gateway_latency_ms"]["p50"] == 900
    o = agg["overall"]
    assert o["pooled"] is True and o["label"] == "pooled across cells (mixture)" and o["cell_count"] == 4
    assert o["requests"] == 5 and o["ok"] == 4 and o["failures"] == {"unsupported_feature": 1}
    assert o["metrics"]["ttft_ms"] == {"n": 0, "unavailable": 4, "note": "no samples"}
    assert o["metrics"]["tpot_ms"]["n"] == 4
    pk = agg["pooled_by_kind_and_cap"]["semantic/cap=16"]
    assert pk["pooled"] is True and "mixture" in pk["note"] and pk["n"] == 3
    assert pk["cells"] == ["p/band=1024/cap=16", "p/band=64/cap=16"]
    assert "[<5 samples]" in bench.markdown_summary(
        {
            "aggregate": agg,
            "settings": {"mode": "t", "case_count": 2, "repeats": 2, "warmup": 0},
            "gateway": "g",
            "started_at": "now",
            "fixture": {"name": "x", "strict": True},
        }  # fmt: skip
    )
    md = bench.markdown_summary(
        {
            "aggregate": agg,
            "settings": {"mode": "t", "case_count": 2, "repeats": 2, "warmup": 0},
            "gateway": "g",
            "started_at": "now",
            "fixture": {"name": "x", "strict": True},
        }  # fmt: skip
    )
    assert "## overall — pooled across cells (mixture)" in md and "## cell p/band=64/cap=16: n=2" in md
    assert "## pooled semantic/cap=16" in md and "- pooled across cells (mixture): not a cell" in md


# --------------------------------------------------------------------------- B1 + B6 evidence
def test_snapshot_evidence_creates_directories_and_counts_trace_states_honestly(tmp_path):
    api = FakeApi(lag=2)
    before = bench.snapshot_evidence(api, "dev_1", [], tmp_path / "run" / "before", reconcile_rounds=0)
    assert (tmp_path / "run" / "before" / "evidence.json").is_file()  # B1: parents created
    assert before["attempted"] is True and before["telemetry_samples"] == 3
    assert before["traces"] == {**before["traces"], "requested": 0, "nonempty": 0, "empty": 0, "error": 0}
    assert before["traces"]["partial"] is False
    after = bench.snapshot_evidence(
        api, "dev_1", ["tr_ok", "tr_lag", "tr_empty", "tr_err", "tr_ok"], tmp_path / "run",
        reconcile_rounds=3, reconcile_wait_s=0.0,
    )  # fmt: skip
    tr = after["traces"]
    assert tr["requested"] == 4 and tr["nonempty"] == 2 and tr["empty"] == 1 and tr["error"] == 1
    assert tr["partial"] is True and sorted(tr["missing_trace_ids"]) == ["tr_empty", "tr_err"]
    assert tr["reconciliation"] == {"rounds_used": 3, "max_rounds": 3, "wait_s": 0.0, "completed": True}
    assert "traces_fetched" not in after and "fetched" not in after
    assert api.calls.count("/api/v1/traces/tr_lag") == 3 and api.calls.count("/api/v1/traces/tr_ok") == 1
    ev = json.loads((tmp_path / "run" / "evidence.json").read_text())
    assert ev["trace_states"] == {
        "tr_ok": "nonempty",
        "tr_lag": "nonempty",
        "tr_empty": "empty",
        "tr_err": "error",
    }
    assert "error" in ev["traces"]["tr_err"] and ev["traces"]["tr_empty"]["spans"] == []
    complete = bench.snapshot_evidence(
        FakeApi(lag=0), "dev_1", ["tr_ok"], tmp_path / "c", reconcile_wait_s=0.0
    )
    assert complete["traces"]["partial"] is False and complete["traces"]["reconciliation"]["rounds_used"] == 0
    assert bench.snapshot_evidence(None, None, ["tr_ok"], tmp_path / "n")["attempted"] is False


def test_api_backed_run_writes_before_evidence_and_trace_counts(tmp_path, monkeypatch):
    gw = FakeGateway()
    gw.header_trace = gw.body_trace = "tr_ok"
    fake = FakeApi(lag=0)
    monkeypatch.setattr(bench, "Api", lambda server, token, ca_file: fake)
    (tmp_path / "token").write_text("secret\n")
    out = tmp_path / "nested" / "bench"
    try:
        rc = bench.main(_args(**{"--out": str(out), "--gateway": gw.url, "--cases": "smoke", "--repeats": "1",
                                 "--server": "https://cp", "--token-file": str(tmp_path / "token"),
                                 "--device-id": "dev_1", "--trace-reconcile-wait-s": "0"}))  # fmt: skip
    finally:
        gw.stop()
    assert rc == 0
    assert (out / "before" / "evidence.json").is_file() and (out / "evidence.json").is_file()
    report = json.loads((out / "report.json").read_text())
    assert (
        report["evidence_before"]["attempted"] is True and report["evidence_before"]["telemetry_samples"] == 3
    )
    tr = report["evidence_after"]["traces"]
    assert tr == {**tr, "requested": 1, "nonempty": 1, "empty": 0, "error": 0, "partial": False}
    assert "traces requested 1, nonempty 1, empty 0, error 0 — complete" in (out / "summary.md").read_text()


# --------------------------------------------------------------------------- B2 typed failures, persistence, partial reports
def test_transport_and_protocol_failures_become_typed_records_not_exceptions():
    case = {
        "id": "c",
        "kind": "semantic",
        "system": "s",
        "prompt": "p",
        "scoring": "exact",
        "expected": "Paris",
    }
    gw = FakeGateway()
    try:
        gw.mode = "hang"
        gw.hang_s = 1.5
        t0 = time.monotonic()
        r = bench.measure_one(gw.url, case, 8, timeout=0.4)
        assert r["ok"] is False and r["error_type"] == "client_timeout_no_response" and r["status"] is None
        assert 350 <= r["client_wall_ms"] <= 1400 and time.monotonic() - t0 < 1.4
        assert "NOT a gateway cancellation" in r["error_message"] and r["client_timeout_s"] == 0.4
        assert r["evidence_defects"] == ["missing_header_trace_id"] and r["trace_id"] is None
        gw.mode = "malformed"
        r = bench.measure_one(gw.url, case, 8, timeout=5)
        assert r["ok"] is False and r["error_type"] == "malformed_json" and r["status"] == 200
        assert r["response_raw"] == "{not json" and r["trace_id"] == "tr_hdr" and r["evidence_defects"] == []
        gw.mode = "http_503"
        r = bench.measure_one(gw.url, case, 8, timeout=5)
        assert r["ok"] is False and r["status"] == 503 and r["error_type"] == "unavailable"
        assert r["trace_id_header"] == "tr_hdr" and r["trace_id"] == "tr_hdr" and r["trace_id_body"] is None
        assert json.loads(r["response_raw"])["error"]["type"] == "unavailable"
    finally:
        gw.stop()
    r = bench.measure_one(f"http://127.0.0.1:{_closed_port()}", case, 8, timeout=2)
    assert r["ok"] is False and r["error_type"] == "connection_error" and r["client_wall_ms"] >= 0
    assert r["request"]["messages"] == [{"role": "system", "content": "s"}, {"role": "user", "content": "p"}]
    assert r["request"]["max_tokens"] == 8 and r["output_cap"] == 8 and r["expected"] == "Paris"


def test_every_request_is_persisted_and_an_interrupt_yields_a_partial_report(tmp_path, monkeypatch):
    gw = FakeGateway()
    out = tmp_path / "bench"
    calls = {"n": 0}
    real = bench.measure_one

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 4:
            raise KeyboardInterrupt
        return real(*a, **kw)

    monkeypatch.setattr(bench, "measure_one", flaky)
    try:
        rc = bench.main(_args(**{"--out": str(out), "--gateway": gw.url, "--cases": "smoke"}))
    finally:
        gw.stop()
    assert rc == 130
    lines = [json.loads(x) for x in (out / "records.jsonl").read_text().splitlines()]
    assert len(lines) == 3 and [x["phase"] for x in lines] == ["warmup", "task", "task"]
    assert all("client_wall_ms" in x and x["request"]["messages"] for x in lines)
    report = json.loads((out / "report.json").read_text())
    assert report["partial"] is True and "KeyboardInterrupt" in report["partial_reason"]
    assert len(report["warmup"]) == 1 and len(report["records"]) == 2
    assert report["aggregate"]["overall"]["requests"] == 2
    assert "PARTIAL REPORT" in (out / "summary.md").read_text()
    assert report["settings"]["client_timeout_s"] == 45.0 and report["settings"]["gateway_deadline_s"] == 30.0
    # an unexpected exception inside the loop is also a partial report, not a lost run
    calls["n"] = 0

    def broken(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(*a, **kw)

    monkeypatch.setattr(bench, "measure_one", broken)
    gw2 = FakeGateway()
    out2 = tmp_path / "bench2"
    try:
        rc = bench.main(_args(**{"--out": str(out2), "--gateway": gw2.url, "--cases": "smoke"}))
    finally:
        gw2.stop()
    report2 = json.loads((out2 / "report.json").read_text())
    assert rc == 1 and report2["partial"] is True and "RuntimeError: boom" in report2["partial_reason"]
    assert len((out2 / "records.jsonl").read_text().splitlines()) == 1


def test_finalize_only_rebuilds_a_partial_report_from_persisted_records(tmp_path):
    gw = FakeGateway()
    out = tmp_path / "bench"
    try:
        assert (
            bench.main(
                _args(**{"--out": str(out), "--gateway": gw.url, "--cases": "smoke", "--repeats": "1"})
            )
            == 0
        )
    finally:
        gw.stop()
    (out / "report.json").unlink()
    assert bench.main(["--finalize-only", "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text())
    assert report["partial"] is True and "finalized from persisted records" in report["partial_reason"]
    assert len(report["records"]) == 8 and len(report["warmup"]) == 8
    assert (
        report["fixture"]["name"] == "convoy-bench-smoke-v1"
        and report["evidence_after"]["attempted"] is False
    )
    assert bench.main(["--finalize-only", "--out", str(tmp_path / "nothing")]) == 2


# --------------------------------------------------------------------------- B3 complete raw responses and trace ids
def test_records_keep_complete_raw_responses_and_flag_trace_id_defects(tmp_path):
    case = {
        "id": "c",
        "kind": "semantic",
        "system": "s",
        "prompt": "p",
        "scoring": "exact",
        "expected": "Paris",
    }
    gw = FakeGateway()
    try:
        r = bench.measure_one(gw.url, case, 8)
        assert r["ok"] and r["content"] == "Paris" and r["finish_reason"] == "stop"
        assert (
            r["trace_id_header"] == "tr_hdr" and r["trace_id_body"] == "tr_hdr" and r["trace_id"] == "tr_hdr"
        )
        assert r["trace_ids_agree"] is True and r["evidence_defects"] == []
        assert (
            r["response_raw_truncated"] is False
            and json.loads(r["response_raw"])["convoy"]["release_id"] == "rel_x"
        )
        assert r["response_bytes"] == len(r["response_raw"].encode())
        assert r["request"] == {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "p"}],
                                "max_tokens": 8, "temperature": 0.0, "seed": 42}  # fmt: skip
        assert r["expected"] == "Paris" and r["scoring"] == "exact" and r["expectation_met"] is True
        assert r["tpot_ms"] == 8.0 and r["tpot_source"].startswith("runtime timings")
        assert r["ttft_ms"] is None and r["ttft_source"] == "unavailable"
        gw.body_trace = "tr_other"
        r = bench.measure_one(gw.url, case, 8)
        assert r["ok"] and r["trace_ids_agree"] is False and r["evidence_defects"] == ["trace_id_mismatch"]
        assert r["trace_id"] == "tr_hdr"  # header is canonical
        gw.header_trace = None
        gw.body_trace = "tr_body"
        r = bench.measure_one(gw.url, case, 8)
        assert r["trace_id"] == "tr_body" and r["evidence_defects"] == ["missing_header_trace_id"]
        gw.header_trace = gw.body_trace = "tr_hdr"
        gw.pad_bytes = bench.RAW_BODY_BOUND + 5000
        r = bench.measure_one(gw.url, case, 8)
        assert r["ok"] and r["response_raw_truncated"] is True and r["content"] == "Paris"
        assert len(r["response_raw"].encode()) <= bench.RAW_BODY_BOUND < r["response_bytes"]
        # defects reach the report's evidence_defects list, warmups included
        gw.pad_bytes = 0
        gw.body_trace = "tr_mismatch"
        out = tmp_path / "bench"
        rc = bench.main(
            _args(**{"--out": str(out), "--gateway": gw.url, "--cases": "smoke", "--repeats": "1"})
        )
    finally:
        gw.stop()
    assert rc == 0
    report = json.loads((out / "report.json").read_text())
    assert len(report["evidence_defects"]) == 16 and {d["phase"] for d in report["evidence_defects"]} == {
        "warmup",
        "task",
    }
    assert all(
        d["defect"] == "trace_id_mismatch" and d["trace_id_header"] == "tr_hdr"
        for d in report["evidence_defects"]
    )
    assert "Evidence defects: 16" in (out / "summary.md").read_text()
    assert report["settings"]["raw_body_bound_bytes"] == 65536


# --------------------------------------------------------------------------- B4 render check
def test_local_qwen3_nonthinking_renderer_matches_the_template_semantics():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    assert bench.render_qwen3_nonthinking(msgs) == (
        "<|im_start|>system\nS<|im_end|>\n<|im_start|>user\nU<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )
    prior = [{"role": "user", "content": "U1"}, {"role": "assistant", "content": "<think>\nhmm\n</think>\n\nA1"}, {"role": "user", "content": "U2"}]  # fmt: skip
    assert bench.render_qwen3_nonthinking(prior) == (
        "<|im_start|>user\nU1<|im_end|>\n<|im_start|>assistant\nA1<|im_end|>\n<|im_start|>user\nU2<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )
    assert bench.render_qwen3_nonthinking(msgs, add_generation_prompt=False).endswith("U<|im_end|>\n")
    assert bench.render_qwen3_nonthinking([{"role": "tool", "content": "x"}]) == bench.GEN_PROMPT


def test_render_check_is_ok_only_for_the_complete_expected_rendering(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("k123\n")
    rt = FakeRuntime("k123")
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "U"}]
    try:
        res = bench.render_check(rt.url, str(key_file), msgs, str(TEMPLATE))
        assert res["ok"] is True and res["reason"].startswith("ok") and res["prompt_tokens"] == 5
        assert res["template_sha256"] == hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()
        assert res["prompt"] == res["expected_prompt"] and res["ends_with_empty_think"] is True
        assert res["checks"] == {"apply_template_status": 200, "ends_with_empty_think": True,
                                 "rendered_equals_expected": True, "tokenize_status": 200, "tokens_nonempty_ints": True}  # fmt: skip
        rt.mode = "wrong_suffix"
        res = bench.render_check(rt.url, str(key_file), msgs, str(TEMPLATE))
        assert res["ok"] is False and res["reason"].startswith("rendered_prompt_mismatch")
        assert (
            res["ends_with_empty_think"] is False
            and "first_difference_at" in res
            and "prompt_tokens" not in res
        )
        rt.mode = "empty_tokens"
        res = bench.render_check(rt.url, str(key_file), msgs, str(TEMPLATE))
        assert (
            res["ok"] is False
            and res["reason"].startswith("tokenize_failed")
            and res["prompt_tokens"] is None
        )
        assert (
            res["checks"]["rendered_equals_expected"] is True
            and res["checks"]["tokens_nonempty_ints"] is False
        )
        rt.mode = "bad_tokens"
        res = bench.render_check(rt.url, str(key_file), msgs, str(TEMPLATE))
        assert res["ok"] is False and res["reason"].startswith("tokenize_failed")
        rt.mode = "pass"
        res = bench.render_check(rt.url, str(key_file), msgs, str(tmp_path / "missing.jinja"))
        assert res["ok"] is False and res["reason"].startswith("template_missing")
        bad_key = tmp_path / "bad"
        bad_key.write_text("wrong")
        res = bench.render_check(rt.url, str(bad_key), msgs, str(TEMPLATE))
        assert (
            res["ok"] is False
            and res["reason"].startswith("apply_template_failed")
            and res["checks"]["apply_template_status"] == 401
        )
        # CLI: --template is real and required; non-zero exit on failure
        assert bench.main(["--render-check", "--runtime", rt.url, "--api-key-file", str(key_file)]) == 2
        assert bench.main(["--render-check", "--runtime", rt.url, "--api-key-file", str(key_file), "--template", str(TEMPLATE), "--system", "S", "--prompt", "U"]) == 0  # fmt: skip
        rt.mode = "wrong_suffix"
        assert bench.main(["--render-check", "--runtime", rt.url, "--api-key-file", str(key_file), "--template", str(TEMPLATE)]) == 1  # fmt: skip
    finally:
        rt.stop()
    res = bench.render_check(f"http://127.0.0.1:{_closed_port()}", str(key_file), msgs, str(TEMPLATE))
    assert res["ok"] is False and res["reason"].startswith("apply_template_transport")


# --------------------------------------------------------------------------- end to end through a real gateway
@pytest.mark.timeout(120)
def test_end_to_end_through_a_real_gateway_writes_an_honest_report(stub, tmp_path):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        op = stub.deploy_op("op_a", "rel_a", 1, expected_active=None)
        assert run_op(a, op)["status"] == "succeeded"
        out = tmp_path / "bench"
        rc = bench.main(_args(**{"--gateway": f"http://127.0.0.1:{a.gw.port}", "--out": str(out), "--smoke": True,
                                 "--repeats": "2", "--warmup": "1", "--label": "simulated runtime (not hardware)"}))  # fmt: skip
        report = json.loads((out / "report.json").read_text())
        o = report["aggregate"]["overall"]
        assert rc == 0 and o["requests"] == 16 and o["ok"] == 16 and o["pooled"] is True
        assert (
            len(report["warmup"]) == 8
            and report["settings"]["case_count"] == 8
            and report["settings"]["mode"] == "smoke"
        )
        assert report["fixture"]["name"] == "convoy-bench-smoke-v1" and report["fixture"]["strict"] is True
        assert (
            report["fixture"]["sha256"]
            == hashlib.sha256((REPO / "scripts/jetson/bench_smoke_v1.jsonl").read_bytes()).hexdigest()
        )
        assert report["schema"] == "convoy-bench-2" and report["partial"] is False
        assert len(report["aggregate"]["cells"]) == 8 and all(
            c["n"] == 2 for c in report["aggregate"]["cells"].values()
        )
        assert len((out / "records.jsonl").read_text().splitlines()) == 24
        r = report["records"][0]
        assert r["trace_id"].startswith("tr_") and r["trace_ids_agree"] is True and r["simulated"] is True
        assert r["trace_id_header"] == r["trace_id_body"] == r["trace_id"] and r["evidence_defects"] == []
        assert r["prompt_tokens"] > 0 and r["completion_tokens"] >= 1 and r["gateway_latency_ms"] is not None
        assert r["input_band"] == "unpadded" and r["output_cap"] == 8 and r["phase"] == "task"
        assert json.loads(r["response_raw"])["convoy"]["trace_id"] == r["trace_id"]
        # TTFT is either the gateway's measured value (a number) or explicitly unavailable, never a guess
        assert (r["ttft_source"] == "gateway measured" and isinstance(r["ttft_ms"], (int, float))) or (
            r["ttft_source"] == "unavailable" and r["ttft_ms"] is None
        )
        assert r["tpot_source"].startswith("runtime timings") or r["tpot_source"].startswith("unavailable")
        by_id = {x["case_id"]: x for x in report["records"]}
        assert (
            by_id["sem-capital-france"]["content"] == "Paris"
            and by_id["sem-capital-france"]["expectation_met"] is True
        )
        assert by_id["sem-bicycle-wheels"]["expectation_met"] is False  # strict: the simulator's prose fails
        assert report["evidence_defects"] == []
        assert (
            report["evidence_after"]["attempted"] is False
            and "not fetched" in report["evidence_after"]["reason"]
        )
        md = (out / "summary.md").read_text()
        assert "Synthetic probes" in md and "(n=2" in md and "(n=16" in md and "[<5 samples]" in md
        assert (
            "pooled across cells (mixture)" in md
            and "cell sem-capital-france/band=unpadded/cap=16: n=2" in md
        )
        assert any("cache_prompt" in h for h in report["honesty"])
        # a gateway that refuses (eval mode) is a counted failure with the header trace id, not a crash
        a.gw.set_mode("eval")
        rec = bench.measure_one(f"http://127.0.0.1:{a.gw.port}", bench.BUILTIN_CASES[0], 8)
        assert rec["ok"] is False and rec["status"] == 503 and rec["error_type"] == "unavailable"
        assert rec["trace_id_header"].startswith("tr_") and rec["trace_id"] == rec["trace_id_header"]
        assert rec["evidence_defects"] == [] and rec["trace_id_body"] is None
        a.gw.set_mode("production")
    finally:
        a.shutdown()


@pytest.mark.timeout(120)
def test_matrix_mode_end_to_end_records_bands_caps_and_actual_tokens_per_cell(stub, tmp_path):
    stub.add_release("rel_a")
    a = boot(make_agent(tmp_path, stub))
    try:
        op = stub.deploy_op("op_a", "rel_a", 1, expected_active=None)
        assert run_op(a, op)["status"] == "succeeded"
        out = tmp_path / "bench"
        rc = bench.main(_args(**{"--gateway": f"http://127.0.0.1:{a.gw.port}", "--out": str(out), "--matrix": True,
                                 "--probe": "sem-capital-france", "--warmup": "1", "--label": "matrix (simulated)"}))  # fmt: skip
        report = json.loads((out / "report.json").read_text())
        assert (
            rc == 0
            and report["settings"]["mode"] == "matrix"
            and report["settings"]["probes"] == ["sem-capital-france"]
        )
        assert report["settings"]["input_bands"] == [64, 256, 1024] and report["settings"]["output_caps"] == [
            16,
            64,
            128,
        ]
        assert report["settings"]["filler_sha256"] == hashlib.sha256(bench.FILLER.encode()).hexdigest()
        cells = report["aggregate"]["cells"]
        assert len(cells) == 9 and all(c["n"] == 3 and c["ok"] == 3 for c in cells.values())
        assert len(report["records"]) == 27 and len(report["warmup"]) == 9
        c64 = cells["sem-capital-france/band=64/cap=16"]
        c1024 = cells["sem-capital-france/band=1024/cap=128"]
        assert c64["input_band"] == 64 and c64["output_cap"] == 16 and c1024["output_cap"] == 128
        assert (
            c64["actual_prompt_tokens"]["n"] == 3
            and c1024["actual_prompt_tokens"]["p50"] > c64["actual_prompt_tokens"]["p50"]
        )
        assert all(r["request"]["max_tokens"] == r["output_cap"] for r in report["records"])
        assert all(r["prompt_tokens"] and r["expectation_met"] is True for r in report["records"])
        md = (out / "summary.md").read_text()
        assert (
            "input band target 1024 -> actual prompt tokens p50" in md
            and "## cell sem-capital-france/band=256/cap=64: n=3" in md
        )
    finally:
        a.shutdown()


def test_interrupt_during_the_response_read_persists_the_in_flight_attempt(tmp_path, monkeypatch):
    """Ctrl-C while reading a response: the attempt (with the header trace id already received) is
    recorded and kept in the partial report; it says nothing about gateway cancellation."""
    import io

    class _Resp(io.BytesIO):
        status = 200
        headers = {"X-Convoy-Trace-Id": "tr_inflight"}

        def read(self, *a, **kw):  # noqa: D401
            raise KeyboardInterrupt

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with monkeypatch.context() as mp:  # scoped: the run-level half below must use the real transport
        mp.setattr(bench.urllib.request, "urlopen", lambda *a, **kw: _Resp())
        with pytest.raises(bench.InterruptedRequest) as ei:
            bench.measure_one("http://127.0.0.1:1", bench.BUILTIN_CASES[0], 8)
    rec = ei.value.record
    assert (
        rec["error_type"] == "interrupted_client"
        and rec["trace_id_header"] == "tr_inflight"
        and rec["trace_id"] == "tr_inflight"
    )
    assert (
        rec["ok"] is False
        and "NOT a gateway cancellation" in rec["error_message"]
        and rec["client_wall_ms"] >= 0
    )
    # run level: the interrupted attempt lands in records.jsonl and the partial report, keeping the
    # phase it was actually in: the plan is warmup, task#0, task#1, ... per case, so interrupting the
    # 3rd attempt hits task repeat 1 and interrupting the 1st hits the warmup
    real = bench.measure_one

    def interrupted_run(nth: int, out):
        gw = FakeGateway()
        calls = {"n": 0}

        def flaky(*a, **kw):
            calls["n"] += 1
            if calls["n"] == nth:
                raise bench.InterruptedRequest(
                    {
                        "case_id": "x",
                        "ok": False,
                        "error_type": "interrupted_client",
                        "trace_id_header": "tr_inflight",
                        "trace_id": "tr_inflight",
                        "client_wall_ms": 12.0,
                        "request": {"messages": []},
                    }
                )
            return real(*a, **kw)

        with monkeypatch.context() as mp:
            mp.setattr(bench, "measure_one", flaky)
            try:
                rc = bench.main(_args(**{"--out": str(out), "--gateway": gw.url, "--cases": "smoke"}))
            finally:
                gw.stop()
        assert rc == 130
        lines = [json.loads(x) for x in (out / "records.jsonl").read_text().splitlines()]
        report = json.loads((out / "report.json").read_text())
        assert report["partial"] is True and "during a request" in report["partial_reason"]
        return lines, report

    lines, report = interrupted_run(3, tmp_path / "bench")
    assert len(lines) == 3 and lines[-1]["error_type"] == "interrupted_client"
    assert lines[-1]["phase"] == "task" and lines[-1]["repeat"] == 1  # a timed attempt stays timed
    assert any(r.get("error_type") == "interrupted_client" for r in report["records"])
    assert not any(r.get("error_type") == "interrupted_client" for r in report["warmup"])

    lines, report = interrupted_run(1, tmp_path / "bench-warmup")
    assert len(lines) == 1 and lines[-1]["error_type"] == "interrupted_client"
    assert lines[-1]["phase"] == "warmup" and lines[-1]["repeat"] == 0  # a warmup stays a warmup
    assert any(r.get("error_type") == "interrupted_client" for r in report["warmup"])
    assert not any(r.get("error_type") == "interrupted_client" for r in report["records"])
    assert not any(r.get("phase") == "interrupted" for r in lines)  # interruption is not a phase
