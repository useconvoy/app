"""Model sources: public Hugging Face (API-resolved, no tokens), operator-supplied provenance, and the
simulator fixture registry (real bytes, real hashes). URLs always pass the shared URL policy."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
from convoy_agent import gguf
from convoy_agent.urlpolicy import UrlPolicyError, hf_resolve_url, redirect_allowed, validate_download_url
from sqlalchemy.orm import Session as DbSession

from .config import Settings
from .ids import iso, utcnow
from .models import FixtureModel


@dataclass
class ResolvedFile:
    path: str
    size: int
    sha256: str
    url: str
    auth: str  # none | device


@dataclass
class Resolved:
    source: str  # hf | fixture
    repo: str
    revision: str
    commit: str
    files: list[ResolvedFile]
    metadata: dict[str, Any]
    verified: str  # source_api | supplied | fixture


class ModelSourceError(Exception):
    pass


def _check_files(files: list[str]) -> None:
    if not files:
        raise ModelSourceError("select at least one .gguf file")
    if len(files) != 1:
        raise ModelSourceError("MVP releases pin exactly one single-file GGUF")


def _default_client() -> httpx.Client:
    return httpx.Client(timeout=30, follow_redirects=False)


# tests replace this with a client over httpx.MockTransport; production never follows redirects blindly
http_client: Callable[[], httpx.Client] = _default_client


class HFSource:
    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.settings = settings
        self.client = client or http_client()

    def resolve(self, repo: str, revision: str, files: list[str]) -> Resolved:
        _check_files(files)
        base = self.settings.hf_endpoint.rstrip("/")
        try:
            r = self.client.get(f"{base}/api/models/{repo}/revision/{revision}")
        except Exception as e:
            raise ModelSourceError(f"huggingface.co unreachable: {type(e).__name__}") from e
        if r.status_code != 200:
            raise ModelSourceError(f"hf revision lookup failed: HTTP {r.status_code}")
        info = r.json()
        if info.get("private") or info.get("gated"):
            raise ModelSourceError("only public ungated repositories are approved")
        commit = info.get("sha") or revision
        tree = self.client.get(f"{base}/api/models/{repo}/tree/{commit}", params={"recursive": "true"})
        if tree.status_code != 200:
            raise ModelSourceError(f"hf tree lookup failed: HTTP {tree.status_code}")
        by_path = {e["path"]: e for e in tree.json() if e.get("type") == "file"}
        out: list[ResolvedFile] = []
        for f in files:
            e = by_path.get(f)
            if not e:
                raise ModelSourceError(f"file not in repo at {commit}: {f}")
            lfs = e.get("lfs") or {}
            sha = lfs.get("oid")
            size = int(lfs.get("size") or e.get("size") or 0)
            if not sha or len(sha) != 64:
                raise ModelSourceError(
                    f"{f} is not an LFS object with a sha256; only LFS GGUF files are approved"
                )
            try:
                url = hf_resolve_url(repo, commit, f)
            except UrlPolicyError as ex:
                raise ModelSourceError(str(ex)) from ex
            out.append(ResolvedFile(path=f, size=size, sha256=sha.lower(), url=url, auth="none"))
        meta = {
            "card": {k: info.get(k) for k in ("id", "sha", "lastModified", "private", "gated") if k in info}
        }
        return Resolved("hf", repo, revision, commit, out, meta, verified="source_api")


class SuppliedSource:
    """Operator-pinned metadata fallback (§ row 18): repo, 40-hex commit, single file, size, sha256."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def resolve(self, repo: str, revision: str, files: list[dict[str, Any]]) -> Resolved:
        if len(files) != 1:
            raise ModelSourceError("supply exactly one file with path, size and sha256")
        f = files[0]
        try:
            sha = str(f.get("sha256", "")).lower()
            size = int(f.get("size", 0))
        except (TypeError, ValueError) as e:
            raise ModelSourceError("size must be an integer") from e
        if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha) or size <= 0:
            raise ModelSourceError("supplied file needs a 64-hex sha256 and a positive size")
        try:
            url = hf_resolve_url(repo, revision, f["path"])
        except (UrlPolicyError, KeyError) as e:
            raise ModelSourceError(str(e)) from e
        return Resolved(
            "hf",
            repo,
            revision,
            revision,
            [ResolvedFile(path=f["path"], size=size, sha256=sha, url=url, auth="none")],
            {},
            verified="supplied",
        )


class FixtureSource:
    def __init__(self, settings: Settings, db: DbSession):
        self.settings = settings
        self.db = db

    def resolve(self, repo: str, revision: str, files: list[str]) -> Resolved:
        _check_files(files)
        row = self.db.get(FixtureModel, (repo, revision))
        if not row:
            raise ModelSourceError(f"fixture model not found: {repo}@{revision}")
        by_path = {f["path"]: f for f in row.files}
        out: list[ResolvedFile] = []
        for f in files:
            e = by_path.get(f)
            if not e:
                raise ModelSourceError(f"file not in fixture: {f}")
            out.append(
                ResolvedFile(
                    path=f,
                    size=int(e["size"]),
                    sha256=e["sha256"],
                    url=f"/api/sim/blobs/{e['sha256']}",
                    auth="device",
                )
            )
        return Resolved(
            "fixture", repo, revision, revision, out, dict(row.metadata_ or {}), verified="fixture"
        )


def write_blob(settings: Settings, data: bytes, subdir: str = "fixtures") -> tuple[str, int, Path]:
    sha = hashlib.sha256(data).hexdigest()
    d = settings.artifacts_dir / subdir
    d.mkdir(parents=True, exist_ok=True)
    p = d / sha
    if not p.exists():
        tmp = p.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(p)
    return sha, len(data), p


def blob_path(settings: Settings, sha: str, subdir: str = "fixtures") -> Path:
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ValueError("bad sha")
    return settings.artifacts_dir / subdir / sha


# ---------------------------------------------------------------- bounded GGUF header inspection ----
HEADER_MAX_BYTES = 32 << 20  # tokenizer arrays of a 150k-vocab model are a few MiB; never more than this
HEADER_CHUNK = 1 << 20


_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+)$")
MAX_REDIRECTS = 4


class RangeReader:
    """Binary stream over HTTP Range requests, bounded to `max_bytes`, redirects only to approved hosts.
    The GGUF parser pulls exactly the header bytes it needs; nothing else is transferred.

    Every response is STREAMED: status, Content-Range (exact start/end, one consistent total) and any
    Content-Length are validated before a single body byte is consumed, the body is bounded per response
    (never more than asked) and in total (`max_bytes`) while it is read, and the response is closed on
    every path. A server that ignores Range, lies about the range or over-delivers costs at most one
    transport chunk, never a whole file."""

    def __init__(
        self, client: httpx.Client, url: str, max_bytes: int = HEADER_MAX_BYTES, chunk: int = HEADER_CHUNK
    ):
        try:
            validate_download_url(url)
        except UrlPolicyError as e:
            raise ModelSourceError(f"header fetch refused: {e}") from e
        self.client = client
        self.url = url
        self.max_bytes = max_bytes
        self.chunk = chunk
        self.buf = b""
        self.pos = 0  # absolute offset of buf[0]
        self.total: int | None = None
        self.bytes_read = 0
        self.final_url = url

    def _check_range_headers(self, r: httpx.Response, start: int, end: int) -> int:
        """Validate a 206 before any body is consumed; returns the exact byte count the body must carry."""
        if r.status_code != 206:
            raise ModelSourceError(f"header fetch: HTTP {r.status_code} (range requests not honoured)")
        m = _CONTENT_RANGE.match(r.headers.get("content-range", "").strip())
        if not m:
            raise ModelSourceError(
                f"header fetch: missing or malformed Content-Range {r.headers.get('content-range')!r}"
            )
        cs, ce, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if self.total is not None and total != self.total:
            raise ModelSourceError(f"header fetch: Content-Range total changed ({self.total} -> {total})")
        if cs != start or ce < cs or ce >= total:
            raise ModelSourceError(
                f"header fetch: Content-Range {cs}-{ce}/{total} does not match the request {start}-{end}"
            )
        # a correct server clamps the end only to the last byte of the file
        if ce != min(end, total - 1):
            raise ModelSourceError(
                f"header fetch: Content-Range {cs}-{ce}/{total} does not match the request {start}-{end}"
            )
        expected = ce - cs + 1
        cl = r.headers.get("content-length")
        if cl is not None:
            try:
                declared = int(cl)
            except ValueError:
                raise ModelSourceError(f"header fetch: malformed Content-Length {cl!r}") from None
            if declared != expected:
                raise ModelSourceError(
                    f"header fetch: Content-Length {declared} disagrees with Content-Range ({expected} bytes)"
                )
        if self.bytes_read + expected > self.max_bytes:
            raise ModelSourceError(f"GGUF header exceeds the {self.max_bytes >> 20} MiB inspection bound")
        self.total = total
        return expected

    def _fetch(self, start: int, end: int) -> bytes:
        url = self.url
        for _ in range(MAX_REDIRECTS):
            with self.client.stream("GET", url, headers={"Range": f"bytes={start}-{end}"}) as r:
                if r.status_code in (301, 302, 303, 307, 308):
                    loc = r.headers.get("location")
                    if not loc or not redirect_allowed(url, loc):
                        raise ModelSourceError(f"header fetch redirect to an unapproved destination: {loc!r}")
                    url = loc
                    continue
                expected = self._check_range_headers(r, start, end)
                parts: list[bytes] = []
                got = 0
                for piece in r.iter_bytes():
                    got += len(piece)
                    if got > expected:
                        raise ModelSourceError(
                            f"header fetch: server sent more than the {expected} bytes requested"
                        )
                    if self.bytes_read + len(piece) > self.max_bytes:
                        raise ModelSourceError(
                            f"GGUF header exceeds the {self.max_bytes >> 20} MiB inspection bound"
                        )
                    self.bytes_read += len(piece)
                    parts.append(piece)
                if got != expected:
                    raise ModelSourceError(f"header fetch: short body ({got} of {expected} bytes)")
                self.final_url = url
                return b"".join(parts)
        raise ModelSourceError("header fetch: too many redirects")

    def read(self, n: int) -> bytes:
        while len(self.buf) < n:
            if self.total is not None and self.pos + len(self.buf) >= self.total:
                break
            start = self.pos + len(self.buf)
            if start + 1 > self.max_bytes:
                raise ModelSourceError(f"GGUF header exceeds the {self.max_bytes >> 20} MiB inspection bound")
            end = min(start + max(self.chunk, n) - 1, self.max_bytes - 1)
            if self.total is not None:
                end = min(end, self.total - 1)
            data = self._fetch(start, end)
            if not data:
                break
            self.buf += data
        out, self.buf = self.buf[:n], self.buf[n:]
        self.pos += len(out)
        return out


def inspect_remote_gguf(
    url: str, expected_size: int | None, client: httpx.Client | None = None
) -> dict[str, Any]:
    """Qualification view of a GGUF from its header only, fetched by bounded HTTP ranges from the pinned
    commit URL. ADVISORY: these bytes are not hash-verified (only the whole file has a pinned sha256),
    so the result is labelled byte_verified=false and the device re-derives everything from the
    verified file at staging and refuses a manifest that contradicts it."""
    reader = RangeReader(client or http_client(), url)
    try:
        meta = gguf.read_metadata_from(reader)  # type: ignore[arg-type]
    except gguf.GGUFError as e:
        raise ModelSourceError(f"remote GGUF header unreadable: {e}") from e
    except httpx.HTTPError as e:
        raise ModelSourceError(f"remote GGUF header unreachable: {type(e).__name__}") from e
    qual = gguf.qualification(meta)
    return {
        "qualification": qual,
        "provenance": {
            "method": "header_range_fetch",
            "url": url,
            "final_url": reader.final_url,
            "bytes_read": reader.bytes_read,
            "header_bytes": meta.get("header_bytes"),
            "content_length": reader.total,
            "size_matches_resolved": (reader.total == int(expected_size))
            if (reader.total is not None and expected_size)
            else None,
            "byte_verified": False,
            "inspected_at": iso(utcnow()),
            "note": "advisory: header bytes fetched by range from the pinned commit, not hash-verified; the device derives the authoritative counts from the sha256-verified file at staging",
        },
    }
