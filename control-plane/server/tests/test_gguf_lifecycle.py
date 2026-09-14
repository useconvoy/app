"""Model metadata provenance: a release records header-derived (advisory) or byte-verified counts,
refuses operator counts that contradict the header, and never turns missing metadata into a fabricated
budget or a dead end. The remote header inspection is bounded and range-based."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile

import httpx
import pytest
from conftest import WEB
from convoy_agent import gguf
from convoy_server import modelsource
from convoy_server.modelsource import ModelSourceError, RangeReader, inspect_remote_gguf

REPO = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
COMMIT = "91cad51170dc346986eccefdc2dd33a9da36ead9"
FILE = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
URL = f"https://huggingface.co/{REPO}/resolve/{COMMIT}/{FILE}"
CDN = f"https://cdn-lfs-us-1.huggingface.co/repos/ab/cd/{COMMIT}"
COMMIT_R = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"


def _gguf_bytes(**over) -> bytes:
    kv = {
        "general.architecture": "qwen2",
        "general.name": "Qwen2.5 1.5B Instruct",
        "general.file_type": 15,
        "qwen2.block_count": 28,
        "qwen2.context_length": 32768,
        "qwen2.embedding_length": 1536,
        "qwen2.attention.head_count": 12,
        "qwen2.attention.head_count_kv": 2,
        "qwen2.vocab_size": 151936,
        "tokenizer.ggml.model": "gpt2",
        "tokenizer.ggml.pre": "qwen2",
        "tokenizer.chat_template": "{% for m in messages %}{{ m.content }}{% endfor %}",
    }
    kv.update(over)
    fd, path = tempfile.mkstemp(suffix=".gguf")
    os.close(fd)
    gguf.write_minimal_gguf(path, kv, pad_bytes=3 << 20)  # "tensor" bytes the header fetch must not read
    data = open(path, "rb").read()
    os.unlink(path)
    return data


class _HF:
    """Mock Hugging Face: API for resolve, a 302 to the CDN for the file, Range-serving CDN."""

    def __init__(self, blob: bytes, *, redirect=True, ranges=True, down=False):
        self.blob, self.redirect, self.ranges, self.down = blob, redirect, ranges, down
        self.requests: list[tuple[str, str | None]] = []
        self.transport = httpx.MockTransport(self.handle)

    def client(self):
        return httpx.Client(transport=self.transport, timeout=5, follow_redirects=False)

    def handle(self, req: httpx.Request) -> httpx.Response:
        rng = req.headers.get("range")
        self.requests.append((str(req.url), rng))
        if self.down:
            raise httpx.ConnectError("unreachable")
        u = str(req.url)
        if u.endswith(f"/api/models/{REPO}/revision/{COMMIT}"):
            return httpx.Response(200, json={"id": REPO, "sha": COMMIT, "private": False, "gated": False})
        if f"/api/models/{REPO}/tree/{COMMIT}" in u:
            return httpx.Response(
                200,
                json=[
                    {
                        "type": "file",
                        "path": FILE,
                        "size": len(self.blob),
                        "lfs": {"oid": hashlib.sha256(self.blob).hexdigest(), "size": len(self.blob)},
                    }
                ],
            )
        if u == URL and self.redirect:
            return httpx.Response(302, headers={"location": CDN})
        if u in (URL, CDN):
            if not rng or not self.ranges:
                return httpx.Response(200, content=self.blob)
            a, b = rng.removeprefix("bytes=").split("-")
            start, end = int(a), min(int(b), len(self.blob) - 1)
            return httpx.Response(
                206,
                content=self.blob[start : end + 1],
                headers={"content-range": f"bytes {start}-{end}/{len(self.blob)}"},
            )
        return httpx.Response(404)


@pytest.fixture()
def hf(monkeypatch):
    blob = _gguf_bytes()
    srv = _HF(blob)
    monkeypatch.setattr(modelsource, "http_client", srv.client)
    return srv


def test_range_reader_reads_only_the_header_through_an_approved_redirect(hf):
    ins = inspect_remote_gguf(URL, len(hf.blob), client=hf.client())
    q, prov = ins["qualification"], ins["provenance"]
    assert q["architecture"] == "qwen2" and q["kv_estimate_inputs"] == {
        "n_layers": 28,
        "n_kv_heads": 2,
        "head_dim": 128,
        "n_embd": 1536,
        "n_vocab": 151936,
    }
    assert (
        prov["method"] == "header_range_fetch" and prov["byte_verified"] is False and prov["final_url"] == CDN
    )
    assert prov["content_length"] == len(hf.blob) and prov["size_matches_resolved"] is True
    assert prov["bytes_read"] < len(hf.blob) and prov["header_bytes"] < (
        1 << 20
    )  # the padding was never fetched
    assert all(r[1] and r[1].startswith("bytes=") for r in hf.requests if r[0] in (URL, CDN))
    # unapproved redirect target, no range support, bound exceeded, unreachable: each an explicit error
    hf.redirect = False
    hf.ranges = False
    with pytest.raises(ModelSourceError, match="range requests not honoured"):
        inspect_remote_gguf(URL, len(hf.blob), client=hf.client())
    hf.ranges = True
    with pytest.raises(ModelSourceError, match="inspection bound"):
        r = RangeReader(hf.client(), URL, max_bytes=64, chunk=16)
        gguf.read_metadata_from(r)  # type: ignore[arg-type]
    with pytest.raises(ModelSourceError):
        RangeReader(hf.client(), "https://evil.example/x.gguf")
    hf.down = True
    with pytest.raises(ModelSourceError, match="unreachable"):
        inspect_remote_gguf(URL, len(hf.blob), client=hf.client())


def _recipe_and_artifact(admin):
    rec = admin.post(
        "/api/v1/recipes",
        json={"name": "r", "commit": COMMIT_R, "tag": "v0.4.0", "backend": "cuda"},
        headers=WEB,
    ).json()
    receipt = {
        "archive_sha256": "ab" * 32,
        "archive_size": 100,
        "files": [{"path": "bin/llama-server", "sha256": "ab" * 32, "size": 10}],
        "provenance": {
            "commit": COMMIT_R,
            "cmake_flags": [
                "-DGGML_CUDA=ON",
                "-DCMAKE_CUDA_ARCHITECTURES=87",
                "-DCMAKE_BUILD_TYPE=Release",
                "-DLLAMA_BUILD_TESTS=OFF",
                "-DLLAMA_BUILD_EXAMPLES=OFF",
                "-DLLAMA_BUILD_SERVER=ON",
                "-DLLAMA_CURL=OFF",
                "-DGGML_NATIVE=OFF",
                "-DLLAMA_USE_PREBUILT_UI=OFF",
                "-DLLAMA_BUILD_UI=OFF",
            ],
            "cuda_version": "12.6.68",
            "l4t_release": "# R36 (release), REVISION: 5.2",
        },
    }
    art = admin.post(
        "/api/v1/runtime-artifacts",
        json={"recipe_id": rec["id"], "receipt": receipt, "scope": "fleet", "storage": "device"},
        headers=WEB,
    )
    assert art.status_code == 201, art.text
    return rec["id"], art.json()["id"]


def _model(hf):
    return {"source": "hf", "repo": REPO, "revision": COMMIT, "files": [FILE]}


def test_wizard_path_records_header_metadata_with_provenance_and_the_resolve_preview_shows_it(app, admin, hf):
    res = admin.post("/api/v1/releases/resolve", json=_model(hf), headers=WEB)
    assert res.status_code == 200, res.text
    assert res.json()["gguf"]["kv_estimate_inputs"]["n_layers"] == 28
    assert (
        res.json()["gguf_provenance"]["method"] == "header_range_fetch"
        and res.json()["gguf_provenance"]["byte_verified"] is False
    )
    rec, art = _recipe_and_artifact(admin)
    r = admin.post(
        "/api/v1/releases",
        json={
            "name": "phys",
            "version": "1",
            "model": _model(hf),
            "recipe_id": rec,
            "runtime_artifact_id": art,
        },
        headers=WEB,
    )
    assert r.status_code == 201, r.text
    rel = admin.get(f"/api/v1/releases/{r.json()['id']}", headers=WEB).json()
    assert rel["weights_qualification"] == "recognized"
    assert (
        rel["model"]["gguf_provenance"]["method"] == "header_range_fetch"
        and rel["model"]["gguf_provenance"]["byte_verified"] is False
    )
    spec_gguf = rel["spec"]["model"]["gguf"] if "spec" in rel else None
    if spec_gguf is not None:
        assert spec_gguf["kv_estimate_inputs"]["n_layers"] == 28
    # the server budget plan names the advisory provenance of the counts
    from helpers import enrolled_agent, heartbeat

    a = enrolled_agent(app, admin)
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": 20000.0})
    b = admin.get(f"/api/v1/releases/{r.json()['id']}/budget/{a.device_id}", headers=WEB).json()
    kv = next(i for i in b["items"] if i["item"] == "kv_cache_mb")
    assert kv["mb"] and "advisory" in kv["source"] and "header_range_fetch" in kv["source"]


def test_forged_operator_counts_are_refused_against_the_header_and_kept_only_labelled_when_no_header(
    app, admin, hf
):
    rec, art = _recipe_and_artifact(admin)
    forged = {
        "architecture": "qwen2",
        "kv_estimate_inputs": {"n_layers": 4, "n_kv_heads": 1, "head_dim": 64},
        "status": "recognized",
        "has_chat_template": True,
    }
    r = admin.post(
        "/api/v1/releases",
        json={
            "name": "forged",
            "version": "1",
            "model": {**_model(hf), "gguf": forged},
            "recipe_id": rec,
            "runtime_artifact_id": art,
        },
        headers=WEB,
    )
    assert r.status_code == 422 and "contradicts the file header" in r.text and "n_layers: 4 vs 28" in r.text
    agreeing = {
        "architecture": "qwen2",
        "kv_estimate_inputs": {"n_layers": 28, "n_kv_heads": 2, "head_dim": 128},
        "status": "recognized",
        "has_chat_template": True,
    }
    ok = admin.post(
        "/api/v1/releases",
        json={
            "name": "agree",
            "version": "1",
            "model": {**_model(hf), "gguf": agreeing},
            "recipe_id": rec,
            "runtime_artifact_id": art,
        },
        headers=WEB,
    )
    assert ok.status_code == 201, ok.text
    assert (
        admin.get(f"/api/v1/releases/{ok.json()['id']}", headers=WEB).json()["model"]["gguf_provenance"][
            "operator_supplied_agrees"
        ]
        is True
    )
    # header unavailable: operator counts are kept but labelled as unverified
    hf.down = True
    r2 = admin.post(
        "/api/v1/releases",
        json={
            "name": "supplied-only",
            "version": "1",
            "model": {
                "source": "supplied",
                "repo": REPO,
                "revision": COMMIT,
                "supplied_files": [
                    {"path": FILE, "size": len(hf.blob), "sha256": hashlib.sha256(hf.blob).hexdigest()}
                ],
                "gguf": forged,
            },
            "recipe_id": rec,
            "runtime_artifact_id": art,
        },
        headers=WEB,
    )
    assert r2.status_code == 201, r2.text
    prov = admin.get(f"/api/v1/releases/{r2.json()['id']}", headers=WEB).json()["model"]["gguf_provenance"]
    assert prov["method"] == "operator_supplied" and prov["byte_verified"] is False


def test_missing_metadata_is_pending_byte_inspection_not_a_fabricated_budget_nor_a_dead_end(app, admin, hf):
    from helpers import enrolled_agent, heartbeat

    rec, art = _recipe_and_artifact(admin)
    hf.down = True  # the wizard's header inspection fails (network); the release can still be created
    r = admin.post(
        "/api/v1/releases",
        json={
            "name": "nometa",
            "version": "1",
            "model": {
                "source": "supplied",
                "repo": REPO,
                "revision": COMMIT,
                "supplied_files": [
                    {"path": FILE, "size": len(hf.blob), "sha256": hashlib.sha256(hf.blob).hexdigest()}
                ],
            },
            "recipe_id": rec,
            "runtime_artifact_id": art,
        },
        headers=WEB,
    )
    assert r.status_code == 201, r.text
    rel = admin.get(f"/api/v1/releases/{r.json()['id']}", headers=WEB).json()
    assert rel["weights_qualification"] == "needs_qualification"
    prov = rel["model"]["gguf_provenance"]
    assert (
        prov["method"] == "unavailable"
        and prov["admission"] == "pending_byte_inspection"
        and "unreachable" in prov["reason"]
    )
    a = enrolled_agent(app, admin)
    heartbeat(a, telemetry={"mem_total_mb": 7620.0, "mem_available_mb": 6000.0, "disk_free_mb": 20000.0})
    b = admin.get(f"/api/v1/releases/{r.json()['id']}/budget/{a.device_id}", headers=WEB).json()
    assert b["verdict"] == "unknown" and "kv_cache_mb" in b["unknown_items"]
    assert "pending byte inspection" in next(i for i in b["items"] if i["item"] == "kv_cache_mb")["source"]
    # the release is immutable: its digest and spec never change afterwards (no mutation endpoint exists)
    again = admin.get(f"/api/v1/releases/{r.json()['id']}", headers=WEB).json()
    assert json.dumps(again, sort_keys=True) == json.dumps(rel, sort_keys=True)


# ------------------------------------------------------------------- bounded, validated streaming ----
class _CountingStream(httpx.SyncByteStream):
    """Body delivered in small pieces; every byte the reader pulls is counted, every close is counted."""

    def __init__(self, data: bytes, ledger: dict, piece: int):
        self.data, self.ledger, self.piece = data, ledger, piece

    def __iter__(self):
        for i in range(0, len(self.data), self.piece):
            chunk = self.data[i : i + self.piece]
            self.ledger["consumed"] += len(chunk)
            yield chunk

    def close(self) -> None:
        self.ledger["closed"] += 1


class _RangeServer:
    """CDN stand-in whose behaviour is scripted per test: honours or ignores Range, over-delivers, lies in
    Content-Range, changes the advertised total. Serves through counting streams (piece = 16 bytes)."""

    PIECE = 16

    def __init__(self, blob: bytes):
        self.blob = blob
        self.mode = "honest"
        self.totals: list[int] = []
        self.ledger = {"consumed": 0, "closed": 0, "responses": 0}
        self.transport = httpx.MockTransport(self.handle)

    def client(self):
        return httpx.Client(transport=self.transport, timeout=5, follow_redirects=False)

    def _resp(self, status: int, body: bytes, headers: dict[str, str]) -> httpx.Response:
        self.ledger["responses"] += 1
        return httpx.Response(status, headers=headers, stream=_CountingStream(body, self.ledger, self.PIECE))

    def handle(self, req: httpx.Request) -> httpx.Response:
        if str(req.url) == URL:
            return self._resp(302, b"", {"location": CDN})
        rng = req.headers.get("range")
        assert rng and rng.startswith("bytes="), "the reader must always send a Range"
        a, b = rng.removeprefix("bytes=").split("-")
        start, end = int(a), min(int(b), len(self.blob) - 1)
        total = len(self.blob)
        want = self.blob[start : end + 1]
        if self.mode == "ignores_range":
            return self._resp(200, self.blob, {"content-length": str(total)})
        if self.mode == "oversized":  # correct Content-Range, no Content-Length, whole file in the body
            return self._resp(206, self.blob, {"content-range": f"bytes {start}-{end}/{total}"})
        if self.mode == "wrong_start":
            cr = f"bytes {start + 1}-{end + 1}/{total}"
        elif self.mode == "wrong_end":
            cr = f"bytes {start}-{end + self.PIECE}/{total}"
        elif self.mode == "drifting_total":
            t = total if not self.totals else total // 2
            self.totals.append(t)
            cr = f"bytes {start}-{end}/{t}"
        else:
            cr = f"bytes {start}-{end}/{total}"
        return self._resp(206, want, {"content-range": cr, "content-length": str(len(want))})


def _reader(srv: _RangeServer, **kw) -> RangeReader:
    return RangeReader(srv.client(), URL, max_bytes=kw.pop("max_bytes", 64), chunk=kw.pop("chunk", 16))


def test_range_reader_validates_every_response_before_consuming_and_bounds_what_it_reads():
    blob = bytes(range(256)) * 512  # 128 KiB, far more than the 64-byte bound
    assert len(blob) == 131072
    # (a) a server that ignores Range answers 200: refused with NOTHING of the body consumed
    srv = _RangeServer(blob)
    srv.mode = "ignores_range"
    with pytest.raises(ModelSourceError, match="range requests not honoured"):
        _reader(srv).read(4)
    assert srv.ledger["consumed"] == 0
    assert srv.ledger["closed"] == srv.ledger["responses"] == 2  # the redirect and the 200, both closed
    # (b) a 206 that over-delivers (whole file behind a correct Content-Range, no Content-Length):
    # refused, and at most one transport piece beyond the 16 bytes asked was pulled
    srv = _RangeServer(blob)
    srv.mode = "oversized"
    with pytest.raises(ModelSourceError, match="more than the 16 bytes requested"):
        _reader(srv).read(4)
    assert 0 < srv.ledger["consumed"] <= 16 + _RangeServer.PIECE
    assert srv.ledger["closed"] == srv.ledger["responses"]
    # (c) Content-Range that does not match the request: refused before any body byte
    for mode in ("wrong_start", "wrong_end"):
        srv = _RangeServer(blob)
        srv.mode = mode
        with pytest.raises(ModelSourceError, match="does not match the request"):
            _reader(srv).read(4)
        assert srv.ledger["consumed"] == 0, mode
        assert srv.ledger["closed"] == srv.ledger["responses"]
    # ... and a total that changes between responses: the second response is refused unconsumed
    srv = _RangeServer(blob)
    srv.mode = "drifting_total"
    r = _reader(srv)
    assert r.read(4) == blob[:4] and r.total == len(blob) and srv.ledger["consumed"] == 16
    with pytest.raises(ModelSourceError, match="total changed"):
        r.read(16)  # buffer holds 12 bytes; the next fetch advertises a different total
    assert srv.ledger["consumed"] == 16 and srv.ledger["closed"] == srv.ledger["responses"]
    # (d) an honest server, but the header would exceed max_bytes: refused with reads <= max_bytes
    srv = _RangeServer(blob)
    r = _reader(srv, max_bytes=64, chunk=16)
    with pytest.raises(ModelSourceError, match="inspection bound"):
        while True:
            assert r.read(4)
    assert r.bytes_read <= 64 and srv.ledger["consumed"] <= 64
    assert srv.ledger["consumed"] == 64  # the four honest 16-byte ranges, nothing past the bound
    assert srv.ledger["closed"] == srv.ledger["responses"]
    # an honest server: the redirect is followed to the approved CDN and bytes come back exact
    srv = _RangeServer(blob)
    r = _reader(srv, max_bytes=1 << 20, chunk=16)
    assert r.read(40) == blob[:40] and r.final_url == CDN and r.bytes_read == 40
    assert srv.ledger["closed"] == srv.ledger["responses"]
    # a redirect to an unapproved host is refused and closed
    srv = _RangeServer(blob)
    srv.handle = lambda req: srv._resp(302, b"", {"location": "https://evil.example/x.gguf"})
    srv.transport = httpx.MockTransport(srv.handle)
    with pytest.raises(ModelSourceError, match="unapproved destination"):
        _reader(srv).read(4)
    assert srv.ledger["consumed"] == 0 and srv.ledger["closed"] == srv.ledger["responses"] == 1


# ------------------------------------------------------------- the model host never holds the writer ----
def test_stalled_model_host_does_not_hold_the_database_writer(app, admin, hf, monkeypatch):
    """The remote header inspection runs before `POST /releases` takes its write transaction: while a
    stalled Hugging Face CDN keeps one request waiting, an independent writer commits promptly."""
    import threading
    import time

    from conftest import login
    from fastapi.testclient import TestClient

    rec, art = _recipe_and_artifact(admin)
    stall_s = 1.5
    stalled = threading.Event()
    inner = hf.handle

    def slow(req: httpx.Request) -> httpx.Response:
        if req.headers.get("range") and not stalled.is_set():
            stalled.set()
            time.sleep(stall_s)
        return inner(req)

    monkeypatch.setattr(hf, "handle", slow)
    monkeypatch.setattr(hf, "transport", httpx.MockTransport(slow))
    other = login(TestClient(app))
    results: dict[str, object] = {}

    def run(name: str, path: str, body: dict) -> None:
        results[name] = admin.post(path, json=body, headers=WEB)

    for name, path, body in (
        (
            "release",
            "/api/v1/releases",
            {
                "name": "slow",
                "version": "1",
                "model": _model(hf),
                "recipe_id": rec,
                "runtime_artifact_id": art,
            },
        ),
        ("resolve", "/api/v1/releases/resolve", _model(hf)),
    ):
        stalled.clear()
        t = threading.Thread(target=run, args=(name, path, body))
        t.start()
        assert stalled.wait(5), "the model host was never asked for the header"
        t0 = time.monotonic()
        w = other.post(
            "/api/v1/recipes",
            json={"name": f"independent-{name}", "commit": "cd" * 20, "tag": "x", "backend": "cuda"},
            headers=WEB,
        )
        elapsed = time.monotonic() - t0
        assert w.status_code == 201, w.text
        assert elapsed < stall_s / 2, f"independent writer waited {elapsed:.2f}s behind the stalled {name}"
        t.join(10)
        assert not t.is_alive()
    assert results["release"].status_code == 201, results["release"].text  # type: ignore[union-attr]
    assert results["resolve"].status_code == 200, results["resolve"].text  # type: ignore[union-attr]
    assert (
        admin.get(f"/api/v1/releases/{results['release'].json()['id']}", headers=WEB).json()["model"][  # type: ignore[union-attr]
            "gguf_provenance"
        ]["method"]
        == "header_range_fetch"
    )


def test_persist_release_revalidates_references_and_uniqueness_inside_the_transaction(app, admin, hf):
    """What was prepared outside the transaction is re-checked inside it: identical content or the same
    name/version landed in between, or a reference that no longer matches, is refused; nothing is written."""
    from convoy_server.config import get_settings
    from convoy_server.db import session_scope, write_txn
    from convoy_server.services import catalog as cat

    rec, art = _recipe_and_artifact(admin)
    data = {"name": "race", "version": "1", "model": _model(hf), "recipe_id": rec, "runtime_artifact_id": art}
    with session_scope() as db:
        prepared = cat.prepare_release(db, get_settings(), data)
        assert prepared.gguf_meta and prepared.gguf_meta["kv_estimate_inputs"]["n_layers"] == 28
        with write_txn(db):
            cat.persist_release(db, get_settings(), prepared, None)
        # the same prepared content under another name: identical digest (the name is not identity)
        prepared.data = {**data, "name": "race-again"}
        with pytest.raises(cat.CatalogError, match="identical release already exists"):
            with write_txn(db):
                cat.persist_release(db, get_settings(), prepared, None)
        # different content (and a fresh header inspection), same name/version -> refused
        prepared2 = cat.prepare_release(db, get_settings(), {**data, "config": {"seed": 3}})
        with pytest.raises(cat.CatalogError, match="name/version already exists"):
            with write_txn(db):
                cat.persist_release(db, get_settings(), prepared2, None)
        # a reference that no longer matches what was validated is refused before anything else
        prepared2.data = {**prepared2.data, "name": "race-3"}
        prepared2.artifact_sha256 = "00" * 32
        with pytest.raises(cat.CatalogError, match="runtime artifact changed"):
            with write_txn(db):
                cat.persist_release(db, get_settings(), prepared2, None)
    assert [r["name"] for r in admin.get("/api/v1/releases", headers=WEB).json()] == ["race"]


def test_principal_disabled_during_preparation_cannot_persist_the_release(app, admin, hf, monkeypatch):
    """The operator authority is re-read inside the write transaction: a principal disabled by another
    connection after preparation (remote header read) but before persistence writes nothing."""
    import threading
    import time

    from conftest import login, make_user
    from fastapi.testclient import TestClient

    user = make_user(admin, "op-stall@example.com", "operator", "op-password-123")
    operator = login(TestClient(app), "op-stall@example.com", "op-password-123")
    stalled, release_it = threading.Event(), threading.Event()
    real = hf.handle

    def slow(req: httpx.Request) -> httpx.Response:
        if req.headers.get("range") and not stalled.is_set():
            stalled.set()
            assert release_it.wait(10)
        return real(req)

    hf.transport = httpx.MockTransport(slow)
    monkeypatch.setattr(modelsource, "http_client", hf.client)
    rec, art = _recipe_and_artifact(admin)
    result: dict = {}

    def run() -> None:
        r = operator.post(
            "/api/v1/releases",
            json={
                "name": "disabled-mid-flight",
                "version": "1",
                "model": _model(hf),
                "recipe_id": rec,
                "runtime_artifact_id": art,
            },
            headers=WEB,
        )
        result["status"], result["text"] = r.status_code, r.text

    t = threading.Thread(target=run)
    t.start()
    assert stalled.wait(5), "the model host was never asked for the header"
    assert admin.patch(f"/api/v1/users/{user['id']}", json={"disabled": True}, headers=WEB).status_code == 200
    release_it.set()
    t.join(15)
    assert result["status"] == 401, result
    names = [r["name"] for r in admin.get("/api/v1/releases", headers=WEB).json()]
    assert "disabled-mid-flight" not in names
    # a live operator on the same path still succeeds (the recheck is not a blanket refusal)
    ok = admin.post(
        "/api/v1/releases",
        json={
            "name": "still-live",
            "version": "1",
            "model": _model(hf),
            "recipe_id": rec,
            "runtime_artifact_id": art,
        },
        headers=WEB,
    )
    assert ok.status_code == 201, ok.text
    time.sleep(0)
