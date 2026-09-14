from __future__ import annotations

import hashlib
import io
import os
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from convoy_agent import gguf
from convoy_agent.client import Client
from convoy_agent.download import DownloadError, download_verified, extract_archive
from convoy_agent.urlpolicy import UrlPolicyError, validate_download_url


class _Srv:
    def __init__(self, blobs: dict[str, bytes], truncate: dict[str, int] | None = None):
        blobs_ = blobs
        trunc = truncate or {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                key = self.path.rsplit("/", 1)[-1]
                if key not in blobs_:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = blobs_[key]
                if key in trunc:
                    data = data[: trunc[key]]
                rng = self.headers.get("Range")
                start = 0
                if rng and rng.startswith("bytes="):
                    start = int(rng[6:].split("-")[0])
                    self.send_response(206)
                else:
                    self.send_response(200)
                self.send_header("Content-Length", str(len(data) - start))
                self.end_headers()
                self.wfile.write(data[start:])

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()


def test_download_verifies_hash_and_size_atomically(tmp_path):
    data = os.urandom(3 * 1024 * 1024 + 123)
    sha = hashlib.sha256(data).hexdigest()
    srv = _Srv({"a.gguf": data, "b.gguf": data + b"x"})
    try:
        c = Client(srv.base, "cvd_dev_x_secret")
        dest = tmp_path / "a.gguf"
        res = download_verified(
            c,
            url=f"{srv.base}/api/sim/blobs/a.gguf",
            dest=dest,
            expected_sha256=sha,
            expected_size=len(data),
            auth="device",
            server_base=srv.base,
        )
        assert res["sha256"] == sha and dest.exists() and not (tmp_path / "a.gguf.part").exists()
        # cached: second call downloads nothing
        assert download_verified(
            c,
            url=f"{srv.base}/api/sim/blobs/a.gguf",
            dest=dest,
            expected_sha256=sha,
            expected_size=len(data),
            auth="device",
            server_base=srv.base,
        )["cached"]
        # wrong size / wrong hash => DIGEST_MISMATCH and no file left behind
        with pytest.raises(DownloadError) as e:
            download_verified(
                c,
                url=f"{srv.base}/api/sim/blobs/b.gguf",
                dest=tmp_path / "b.gguf",
                expected_sha256=sha,
                expected_size=len(data),
                auth="device",
                server_base=srv.base,
            )
        assert (
            e.value.code == "DIGEST_MISMATCH"
            and not (tmp_path / "b.gguf").exists()
            and not (tmp_path / "b.gguf.part").exists()
        )
        with pytest.raises(DownloadError) as e:
            download_verified(
                c,
                url=f"{srv.base}/api/sim/blobs/a.gguf",
                dest=tmp_path / "c.gguf",
                expected_sha256="0" * 64,
                expected_size=len(data),
                auth="device",
                server_base=srv.base,
            )
        assert e.value.code == "DIGEST_MISMATCH"
    finally:
        srv.stop()


def test_truncated_source_is_a_digest_mismatch_and_partial_removed(tmp_path):
    data = os.urandom(1024 * 1024)
    sha = hashlib.sha256(data).hexdigest()
    srv = _Srv({"t.gguf": data}, truncate={"t.gguf": 500_000})
    try:
        c = Client(srv.base, "x")
        with pytest.raises(DownloadError) as e:
            download_verified(
                c,
                url=f"{srv.base}/api/sim/blobs/t.gguf",
                dest=tmp_path / "t.gguf",
                expected_sha256=sha,
                expected_size=len(data),
                auth="device",
                server_base=srv.base,
                attempts=1,
            )
        assert e.value.code == "DIGEST_MISMATCH" and not (tmp_path / "t.gguf.part").exists()
    finally:
        srv.stop()


def test_disk_full_precheck(tmp_path, monkeypatch):
    import convoy_agent.download as dl

    monkeypatch.setattr(dl, "free_bytes", lambda p: 10)
    with pytest.raises(DownloadError) as e:
        download_verified(
            Client("http://127.0.0.1:1", "x"),
            url="http://127.0.0.1:1/api/sim/blobs/x",
            dest=tmp_path / "x",
            expected_sha256="0" * 64,
            expected_size=10**9,
            auth="device",
            server_base="http://127.0.0.1:1",
        )
    assert e.value.code == "DISK_FULL"


def test_url_policy_rejects_unapproved_sources():
    good = "https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct-GGUF/resolve/91cad51170dc346986eccefdc2dd33a9da36ead9/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    assert validate_download_url(good) == "hf"
    for bad in (
        "https://huggingface.co/Qwen/x/resolve/main/a.gguf",
        "https://hf-mirror.com/a/b/resolve/" + "a" * 40 + "/x.gguf",
        "https://192.168.1.5/x.gguf",
        "https://localhost/x.gguf",
        "file:///etc/passwd",
        "https://huggingface.co/Qwen/x/resolve/" + "a" * 40 + "/x.bin",
        "https://user:pw@huggingface.co/Qwen/x/resolve/" + "a" * 40 + "/x.gguf",
    ):
        with pytest.raises(UrlPolicyError):
            validate_download_url(bad)


def test_gguf_parser_bounds_and_qualification(tmp_path):
    p = tmp_path / "m.gguf"
    gguf.write_minimal_gguf(
        str(p),
        {
            "general.architecture": "qwen2",
            "general.file_type": 15,
            "qwen2.block_count": 28,
            "qwen2.embedding_length": 1536,
            "qwen2.attention.head_count": 12,
            "qwen2.attention.head_count_kv": 2,
            "tokenizer.ggml.model": "gpt2",
            "tokenizer.chat_template": "{{ messages }}",
        },
    )
    q = gguf.qualification(gguf.read_metadata(str(p)))
    assert (
        q["status"] == "recognized"
        and q["file_type"] == "Q4_K_M"
        and q["kv_estimate_inputs"]["head_dim"] == 128
    )
    gguf.write_minimal_gguf(str(p), {"general.architecture": "mystery", "general.file_type": 15})
    q = gguf.qualification(gguf.read_metadata(str(p)))
    assert q["status"] == "needs_qualification" and any("architecture" in r for r in q["reasons"])
    # hostile header: absurd KV count
    import struct

    p.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", 10**9))
    with pytest.raises(gguf.GGUFError):
        gguf.read_metadata(str(p))
    p.write_bytes(b"NOPE" + b"\0" * 32)
    with pytest.raises(gguf.GGUFError):
        gguf.read_metadata(str(p))


def test_extract_archive_rejects_symlinks_traversal_and_verifies_members(tmp_path):
    data = b"#!/bin/sh\nexit 0\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("bin/llama-server")
        ti.size = len(data)
        ti.mode = 0o755
        tf.addfile(ti, io.BytesIO(data))
    arch = tmp_path / "a.tar.gz"
    arch.write_bytes(buf.getvalue())
    files = [{"path": "bin/llama-server", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}]
    out = extract_archive(arch, tmp_path / "rt", files)
    assert out[0]["path"] == "bin/llama-server" and os.access(
        tmp_path / "rt" / "bin" / "llama-server", os.X_OK
    )
    with pytest.raises(DownloadError) as e:
        extract_archive(
            arch, tmp_path / "rt2", [{"path": "bin/llama-server", "sha256": "0" * 64, "size": len(data)}]
        )
    assert e.value.code == "DIGEST_MISMATCH"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ln = tarfile.TarInfo("bin/evil")
        ln.type = tarfile.SYMTYPE
        ln.linkname = "/etc/passwd"
        tf.addfile(ln)
    (tmp_path / "b.tar.gz").write_bytes(buf.getvalue())
    with pytest.raises(DownloadError) as e:
        extract_archive(tmp_path / "b.tar.gz", tmp_path / "rt3", [])
    assert e.value.code == "ARCHIVE_REJECTED"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        ti = tarfile.TarInfo("../escape")
        ti.size = 1
        tf.addfile(ti, io.BytesIO(b"x"))
    (tmp_path / "c.tar.gz").write_bytes(buf.getvalue())
    with pytest.raises(DownloadError):
        extract_archive(tmp_path / "c.tar.gz", tmp_path / "rt4", [])
