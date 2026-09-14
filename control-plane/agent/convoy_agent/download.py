"""Atomic, hash-verified artifact downloads (stdlib).

Rules: URL must pass the shared policy; redirects only to approved HF hosts; write to <name>.part,
fsync file, same-filesystem rename, fsync directory; verify sha256 and size before rename; on any
mismatch delete the partial and raise DIGEST_MISMATCH. Free-space precheck and ENOSPC -> DISK_FULL.
Resumable via HTTP Range when the partial's prefix is already on disk (re-hashed from scratch)."""

from __future__ import annotations

import hashlib
import http.client
import os
import shutil
import ssl
import time
import urllib.error
from pathlib import Path
from typing import Any, Callable

from .client import Client, Transient
from .urlpolicy import UrlPolicyError, redirect_allowed, same_origin, validate_download_url

CHUNK = 1 << 20
DISK_HEADROOM_BYTES = 256 << 20


class DownloadError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def sha256_file(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def free_bytes(path: Path) -> int:
    st = os.statvfs(str(path))
    return st.f_bavail * st.f_frsize


def download_verified(
    client: Client,
    *,
    url: str,
    dest: Path,
    expected_sha256: str,
    expected_size: int,
    auth: str = "none",
    server_base: str | None = None,
    progress: Callable[[int, int], None] | None = None,
    max_redirects: int = 3,
    attempts: int = 4,
) -> dict[str, Any]:
    """Return {'sha256','size','bytes_downloaded','resumed','seconds'}. Raises DownloadError."""
    try:
        kind = validate_download_url(url, server_base=server_base)
    except UrlPolicyError as e:
        raise DownloadError("URL_REJECTED", str(e)) from e
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        sha, size = sha256_file(dest)
        if sha == expected_sha256 and size == expected_size:
            return {
                "sha256": sha,
                "size": size,
                "bytes_downloaded": 0,
                "resumed": False,
                "seconds": 0.0,
                "cached": True,
            }
        dest.unlink()
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have > expected_size:
        part.unlink()
        have = 0
    if free_bytes(dest.parent) < (expected_size - have) + DISK_HEADROOM_BYTES:
        raise DownloadError(
            "DISK_FULL",
            "not enough free disk for the artifact",
            {"free_bytes": free_bytes(dest.parent), "needed_bytes": expected_size - have},
        )
    t0 = time.monotonic()
    downloaded = 0
    resumed = have > 0
    delay = 1.0
    stop = getattr(client, "stop", None)
    for attempt in range(attempts):
        if stop is not None and stop.is_set():
            raise DownloadError(
                "INTERRUPTED",
                "download interrupted by agent shutdown; the partial file is kept for a resumed attempt",
                {"have_bytes": part.stat().st_size if part.exists() else 0, "expected_size": expected_size},
            )
        try:
            downloaded += _fetch(
                client, url, kind, part, have, expected_size, auth, max_redirects, progress, server_base
            )
            break
        except Transient as e:
            if attempt == attempts - 1:
                raise DownloadError(
                    "DOWNLOAD_FAILED", f"download failed after {attempts} attempts: {e}"
                ) from e
            if stop is not None:
                if stop.wait(min(30.0, delay)):
                    continue  # the loop head raises INTERRUPTED with the partial file retained
            else:
                time.sleep(min(30.0, delay))
            delay *= 2
            have = part.stat().st_size if part.exists() else 0
        except OSError as e:
            if e.errno == 28:
                if part.exists():
                    part.unlink()
                raise DownloadError("DISK_FULL", "disk full while writing the artifact") from e
            raise DownloadError("DOWNLOAD_FAILED", f"io error: {e}") from e
    sha, size = sha256_file(part)
    if sha != expected_sha256 or size != expected_size:
        part.unlink()
        raise DownloadError(
            "DIGEST_MISMATCH",
            "downloaded artifact hash or size did not match the release manifest",
            {
                "expected_sha256": expected_sha256,
                "actual_sha256": sha,
                "expected_size": expected_size,
                "actual_size": size,
            },
        )
    os.replace(part, dest)
    fsync_dir(dest.parent)
    return {
        "sha256": sha,
        "size": size,
        "bytes_downloaded": downloaded,
        "resumed": resumed,
        "seconds": round(time.monotonic() - t0, 3),
        "cached": False,
    }


def _fetch(
    client: Client,
    url: str,
    kind: str,
    part: Path,
    have: int,
    expected_size: int,
    auth: str,
    max_redirects: int,
    progress,
    server_base: str | None = None,
) -> int:
    headers = {}
    if have:
        headers["Range"] = f"bytes={have}-"
    cur = url
    resp = None
    for _ in range(max_redirects + 1):
        # R25: the device credential is attached only when the CURRENT hop is exactly the control-plane
        # origin; any hop elsewhere (approved HF hosts) is fetched anonymously
        on_control_plane = bool(server_base) and same_origin(cur, server_base)
        try:
            resp = client.open_stream(
                cur, extra_headers=headers, use_credential=(auth == "device" and on_control_plane)
            )
            break
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                loc = e.headers.get("Location")
                if not loc:
                    raise DownloadError("URL_REJECTED", "redirect without location") from e
                if on_control_plane:
                    raise DownloadError(
                        "URL_REJECTED", "the control plane must not redirect authenticated artifact requests"
                    ) from e
                if not redirect_allowed(cur, loc):
                    raise DownloadError("URL_REJECTED", f"redirect to unapproved destination: {loc!r}") from e
                cur = loc
                continue
            if e.code == 416:
                part.unlink(missing_ok=True)
                raise Transient("range not satisfiable; restarting") from e
            if e.code in (500, 502, 503, 504, 429):
                raise Transient(f"HTTP {e.code}") from e
            raise DownloadError("DOWNLOAD_FAILED", f"HTTP {e.code} from source") from e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            raise Transient(str(e)[:200]) from e
    if resp is None:
        raise DownloadError("URL_REJECTED", "too many redirects")
    status = getattr(resp, "status", 200)
    mode = "ab" if (status == 206 and have) else "wb"
    if mode == "wb":
        have = 0
    n = 0
    with open(part, mode) as f:
        while True:
            try:
                chunk = resp.read(CHUNK)
            except (OSError, TimeoutError, ConnectionError, ssl.SSLError, http.client.HTTPException) as e:
                # a socket-level failure mid-body (peer reset, read timeout, shutdown by a stop) is
                # transient: what was written stays on disk for a Range resume
                raise Transient(f"read failed after {n} bytes: {str(e)[:120]}") from e
            if not chunk:
                break
            f.write(chunk)
            n += len(chunk)
            if have + n > expected_size + CHUNK:
                raise DownloadError(
                    "DIGEST_MISMATCH",
                    "source returned more bytes than the manifest size",
                    {"expected_size": expected_size},
                )
            if progress:
                progress(have + n, expected_size)
        f.flush()
        os.fsync(f.fileno())
    announced_left = getattr(resp, "length", None)
    if announced_left:
        # the connection ended before the announced Content-Length (peer reset, aborted by a stop): a
        # transport failure, not a verdict on the bytes; the partial file is kept for a Range resume.
        # A source that delivers its whole announced body short of the manifest size is judged by the
        # hash check instead (DIGEST_MISMATCH, partial removed).
        raise Transient(f"connection ended after {n} bytes with {announced_left} announced bytes unread")
    return n


def normalize_member(name: str) -> str:
    if name.startswith("/") or "\\" in name:
        raise DownloadError("ARCHIVE_REJECTED", f"unsafe member path {name!r}")
    parts = [seg for seg in name.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        raise DownloadError("ARCHIVE_REJECTED", f"unsafe member path {name!r}")
    return "/".join(parts)


def extract_archive(
    archive: Path,
    dest: Path,
    expected_files: list[dict[str, Any]],
    max_members: int = 512,
    max_total_bytes: int = 2 << 30,
) -> list[dict[str, Any]]:
    """Bounded tar extraction (R26): regular files only, canonical paths, no aliases/duplicates, no
    unlisted members, exact receipt hash/size agreement, count/size limits. Extracts into a temp dir
    and publishes it by rename into an immutable digest directory; never deletes an existing dest."""
    import tarfile

    if dest.exists():
        # immutable digest directory: reconcile instead of destroying
        ok = True
        for f in expected_files:
            p = dest / normalize_member(f["path"])
            if not p.exists() or sha256_file(p)[0] != f.get("sha256"):
                ok = False
                break
        if ok:
            return [
                {"path": normalize_member(f["path"]), "sha256": f.get("sha256"), "size": f.get("size")}
                for f in expected_files
            ]
        raise DownloadError(
            "DIGEST_MISMATCH",
            f"existing runtime directory {dest.name} does not match the receipt; remove it manually",
            {"dest": str(dest)},
        )
    expected = {normalize_member(f["path"]): f for f in expected_files}
    tmp = dest.with_name(dest.name + ".extract")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    seen: dict[str, dict[str, Any]] = {}
    total = 0
    try:
        with tarfile.open(archive, "r:*") as tf:
            for i, m in enumerate(tf):
                if i >= max_members:
                    raise DownloadError("ARCHIVE_REJECTED", "too many archive members")
                if m.isdir():
                    continue
                if not m.isreg():
                    raise DownloadError(
                        "ARCHIVE_REJECTED",
                        f"non-regular member {m.name!r} (symlinks/devices are not allowed)",
                    )
                name = normalize_member(m.name)
                if name in seen:
                    raise DownloadError("ARCHIVE_REJECTED", f"duplicate/aliased member {m.name!r}")
                if name not in expected:
                    raise DownloadError("ARCHIVE_REJECTED", f"member {name!r} is not listed in the receipt")
                total += m.size
                if total > max_total_bytes:
                    raise DownloadError("ARCHIVE_REJECTED", "archive too large")
                out = tmp / name
                out.parent.mkdir(parents=True, exist_ok=True)
                src = tf.extractfile(m)
                assert src is not None
                h = hashlib.sha256()
                with open(out, "wb") as f:
                    while True:
                        chunk = src.read(CHUNK)
                        if not chunk:
                            break
                        h.update(chunk)
                        f.write(chunk)
                    f.flush()
                    os.fsync(f.fileno())
                exp = expected[name]
                if exp.get("sha256") != h.hexdigest() or (
                    exp.get("size") is not None and int(exp["size"]) != m.size
                ):
                    raise DownloadError(
                        "DIGEST_MISMATCH", f"archive member {name} does not match the receipt", {"path": name}
                    )
                if (
                    m.mode & 0o111
                    or name.endswith("llama-server")
                    or name.endswith(".sim")
                    or "/bin/" in name
                ):
                    os.chmod(out, 0o755)
                seen[name] = {"path": name, "sha256": h.hexdigest(), "size": m.size}
        missing = sorted(set(expected) - set(seen))
        if missing:
            raise DownloadError("DIGEST_MISMATCH", f"archive is missing receipt files: {missing[:5]}")
        for d in sorted({str(Path(n).parent) for n in seen}, key=len, reverse=True):
            fsync_dir(tmp / d if d != "." else tmp)
        os.replace(tmp, dest)
        fsync_dir(dest.parent)
    except Exception:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    return list(seen.values())
