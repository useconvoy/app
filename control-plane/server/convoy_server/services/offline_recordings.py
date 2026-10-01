"""Offline episode recordings in the shared recording store.

An offline episode's frames, actions and timings are one server-built SQLite file in the same directory,
under the same lock and the same installation-wide quota (`CONVOY_RECORDING_QUOTA_BYTES`) as hosted
recordings: `recordings/<episode id>.sqlite3`. Each account's offline files are also capped by
`CONVOY_OFFLINE_EVALUATION_QUOTA_BYTES`, so one import cannot take the store that hosted replays need.
The server writes every file itself from validated values; it never accepts a client path or file.

Writes happen inside the API's serialized write transaction. A new file is written as `<id>.partial`
and renamed to `<id>.sqlite3` once its row is committed; a removed one is renamed to `<id>.removed`
before its row is deleted and unlinked after the commit. `sweep` settles whatever an interrupted write
left: a file whose row exists is published (or restored), any other is discarded. Readers accept a
committed row's `.partial` too, so a row never points at a missing file between commit and rename.

Images are checked without an image library: PNG (CRCs, header, bounded inflate of exactly the declared
scanlines) or baseline/progressive JPEG (marker walk to the frame header, complete to EOI), at most
MAX_DIMENSION pixels on each side. Browsers decode them; the server only stores and returns the bytes.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import struct
import zlib
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..offline_models import OfflineEpisode
from .recording_upload import locked_store

MAX_DIMENSION = 320
MAX_IMAGE_BYTES = 256 * 1024
DEFAULT_SHARED_QUOTA = 512 * 1024**2
DEFAULT_ACCOUNT_QUOTA = 128 * 1024**2
# Page and journal overhead allowance, as for hosted recordings.
MARGIN = 64 * 1024
FILE = re.compile(r"(oep_[a-z0-9]{12})\.(partial|sqlite3|removed)")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Channels per colour type, and the bit depths each colour type allows.
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
# Segments a JPEG may carry before its first scan: APPn, COM, DQT, DHT, DRI and the frame header.
JPEG_SEGMENTS = {*range(0xE0, 0xF0), 0xFE, 0xDB, 0xC4, 0xDD}
JPEG_FRAMES = {0xC0, 0xC1, 0xC2}  # baseline, extended and progressive Huffman coding
SCHEMA = """
CREATE TABLE episode(id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL, digest TEXT NOT NULL,
    steps INTEGER NOT NULL, manifest_json TEXT NOT NULL);
CREATE TABLE frames(idx INTEGER PRIMARY KEY, action_json TEXT NOT NULL, reward REAL, success INTEGER,
    policy_ms REAL);
CREATE TABLE images(idx INTEGER PRIMARY KEY, media_type TEXT NOT NULL, data BLOB NOT NULL);
"""


def shared_quota() -> int:
    return int(os.environ.get("CONVOY_RECORDING_QUOTA_BYTES", str(DEFAULT_SHARED_QUOTA)))


def account_quota() -> int:
    return int(os.environ.get("CONVOY_OFFLINE_EVALUATION_QUOTA_BYTES", str(DEFAULT_ACCOUNT_QUOTA)))


def unavailable() -> HTTPException:
    return HTTPException(404, "Recording is not available for this episode")


def storage_full(scope: str) -> HTTPException:
    return HTTPException(507, f"{scope} storage limit reached; delete offline evaluations to free space")


# ---- images --------------------------------------------------------------------------------------


def image(data: bytes) -> tuple[str, int, int]:
    """(media type, width, height) of a bounded PNG or JPEG frame, else ValueError."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"image exceeds {MAX_IMAGE_BYTES} bytes")
    if data.startswith(PNG_SIGNATURE):
        return ("image/png", *_png(data))
    if data.startswith(b"\xff\xd8"):
        return ("image/jpeg", *_jpeg(data))
    raise ValueError("image must be a PNG or a JPEG")


def _dimensions(width: int, height: int) -> tuple[int, int]:
    if not (1 <= width <= MAX_DIMENSION and 1 <= height <= MAX_DIMENSION):
        raise ValueError(f"image is {width}×{height}; each side must be 1–{MAX_DIMENSION} pixels")
    return width, height


def _png(data: bytes) -> tuple[int, int]:
    offset, header, plte, run = 8, None, False, 0  # run: 0 no IDAT yet, 1 inside, 2 ended
    compressed = bytearray()
    while True:
        if offset + 12 > len(data):
            raise ValueError("truncated PNG")
        size = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        end = offset + 12 + size
        if end > len(data) or not kind.isalpha():
            raise ValueError("truncated or invalid PNG chunk")
        content = data[offset + 8 : end - 4]
        if zlib.crc32(kind + content) != struct.unpack(">I", data[end - 4 : end])[0]:
            raise ValueError("invalid PNG CRC")
        if header is None:
            if kind != b"IHDR" or size != 13:
                raise ValueError("PNG must start with its IHDR header")
            width, height, depth, color, compression, filtering, interlace = struct.unpack(">IIBBBBB", content)
            _dimensions(width, height)
            if color not in PNG_CHANNELS or depth not in PNG_DEPTHS[color]:
                raise ValueError("unsupported PNG colour type or bit depth")
            if compression or filtering or interlace:
                raise ValueError("PNG must be non-interlaced with standard compression and filtering")
            header = (width, height, depth, color)
        elif kind == b"IDAT":
            if run == 2:
                raise ValueError("PNG image data must be contiguous")
            run = 1
            compressed += content
        elif kind == b"IEND":
            if size or end != len(data):
                raise ValueError("invalid PNG end")
            break
        else:
            run = 2 if run else 0
            if kind == b"PLTE":
                plte = run == 0  # a palette precedes the image data
            elif kind[:1].isupper():
                raise ValueError(f"unsupported PNG chunk {kind.decode()}")
        offset = end
    width, height, depth, color = header
    if not compressed or (color == 3 and not plte):
        raise ValueError("PNG has no image data or no palette")
    row = (width * PNG_CHANNELS[color] * depth + 7) // 8 + 1
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(bytes(compressed), height * row + 1)
    except zlib.error as error:
        raise ValueError("invalid PNG compression") from error
    if (len(pixels) != height * row or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or max(pixels[::row]) > 4):
        raise ValueError("PNG image data does not match its header")
    return width, height


def _jpeg(data: bytes) -> tuple[int, int]:
    if not data.endswith(b"\xff\xd9"):
        raise ValueError("JPEG must end with its EOI marker")
    offset, size = 2, None
    while True:
        if offset + 4 > len(data) or data[offset] != 0xFF:
            raise ValueError("invalid JPEG marker")
        while data[offset] == 0xFF and offset + 4 < len(data):
            offset += 1  # fill bytes
        marker = data[offset]
        length = struct.unpack(">H", data[offset + 1 : offset + 3])[0]
        segment = data[offset + 3 : offset + 1 + length]
        if length < 2 or offset + 1 + length > len(data):
            raise ValueError("truncated JPEG segment")
        if marker == 0xDA:
            if size is None:
                raise ValueError("JPEG scan precedes its frame header")
            return size
        if marker in JPEG_FRAMES:
            if size is not None or len(segment) < 6:
                raise ValueError("invalid JPEG frame header")
            precision, height, width, components = struct.unpack(">BHHB", segment[:6])
            if precision != 8 or components not in (1, 3) or len(segment) != 6 + 3 * components:
                raise ValueError("JPEG must be 8-bit greyscale or colour")
            size = _dimensions(width, height)
        elif marker not in JPEG_SEGMENTS:
            raise ValueError("unsupported JPEG coding (baseline or progressive Huffman only)")
        offset += 1 + length


# ---- store ---------------------------------------------------------------------------------------


def _files(root: Path, episode_id: str) -> tuple[Path, Path, Path]:
    # Ids come only from database rows (`oep_` + 12 characters), never from a request path.
    return root / f"{episode_id}.partial", root / f"{episode_id}.sqlite3", root / f"{episode_id}.removed"


def _used(root: Path) -> int:
    return sum(path.stat().st_size for path in root.iterdir() if path.is_file())


@contextmanager
def store() -> Iterator[Path]:
    with locked_store() as root:
        yield root


def sweep(db: Session, root: Path) -> None:
    """Under the store lock, inside a write transaction: settle files an interrupted write left behind."""
    found: dict[str, list[tuple[str, Path]]] = {}
    for path in root.iterdir():
        match = FILE.fullmatch(path.name)
        if match:
            found.setdefault(match.group(1), []).append((match.group(2), path))
    if not found:
        return
    ids = sorted(found)
    present: set[str] = set()
    for start in range(0, len(ids), 500):
        present.update(db.scalars(select(OfflineEpisode.id).where(OfflineEpisode.id.in_(ids[start : start + 500]))))
    for episode_id, entries in found.items():
        _, final, _ = _files(root, episode_id)
        for state, path in entries:
            if state == "sqlite3":
                continue
            if episode_id in present and not final.exists():
                os.replace(path, final)  # committed: finish the upload, or undo the removal
            else:
                path.unlink(missing_ok=True)


def write(
    root: Path,
    episode_id: str,
    header: tuple[str, str, int, dict],
    frames: Iterable[tuple[int, list[float], float | None, bool | None, float | None]],
    images: Iterable[tuple[int, str, bytes]],
    account_used: int,
    estimate: int,
) -> int:
    """Writes `<id>.partial` within both quotas and returns its size. The caller holds the store lock."""
    partial, _, _ = _files(root, episode_id)
    shared, account = shared_quota(), account_quota()
    if account_used + estimate + MARGIN > account:
        raise storage_full("Offline evaluation")
    if _used(root) + estimate + MARGIN > shared:
        raise storage_full("Recording")
    evaluation_id, digest, steps, manifest = header
    try:
        db = sqlite3.connect(partial)
        try:
            # A new private file: no rollback journal is needed (an interrupted file is discarded whole).
            db.execute("PRAGMA journal_mode=OFF")
            with db:
                db.executescript(SCHEMA)
                db.execute(
                    "INSERT INTO episode VALUES (?,?,?,?,?)",
                    (episode_id, evaluation_id, digest, steps, json.dumps(manifest, separators=(",", ":"))),
                )
                db.executemany(
                    "INSERT INTO frames VALUES (?,?,?,?,?)",
                    ((index, json.dumps(action, separators=(",", ":")), reward,
                      None if success is None else int(success), policy_ms)
                     for index, action, reward, success, policy_ms in frames),
                )
                db.executemany("INSERT INTO images VALUES (?,?,?)", images)
        finally:
            db.close()
        os.chmod(partial, 0o600)
        with partial.open("rb") as handle:
            os.fsync(handle.fileno())
        size = partial.stat().st_size
        if account_used + size > account:
            raise storage_full("Offline evaluation")
        if _used(root) > shared:
            raise storage_full("Recording")
        return size
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def discard(episode_id: str) -> None:
    """Drops an uncommitted upload's partial file."""
    with store() as root:
        _files(root, episode_id)[0].unlink(missing_ok=True)


def publish(episode_id: str) -> None:
    """After the episode's row is committed: its file becomes the published recording."""
    with store() as root:
        partial, final, _ = _files(root, episode_id)
        if partial.exists():
            os.replace(partial, final)
            _sync(root)


def withdraw(root: Path, episode_ids: Iterable[str]) -> None:
    """Before the rows are deleted (store lock held): the recordings stop being readable."""
    for episode_id in episode_ids:
        _, final, removed = _files(root, episode_id)
        if final.exists():
            os.replace(final, removed)


def restore(episode_ids: Iterable[str]) -> None:
    """The removal did not commit: put the recordings back."""
    with store() as root:
        for episode_id in episode_ids:
            _, final, removed = _files(root, episode_id)
            if removed.exists() and not final.exists():
                os.replace(removed, final)


def remove(episode_ids: Iterable[str]) -> None:
    """After the removal committed: free the space."""
    with store() as root:
        for episode_id in episode_ids:
            for path in _files(root, episode_id):
                path.unlink(missing_ok=True)
        _sync(root)


def _sync(root: Path) -> None:
    descriptor = os.open(root, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


# ---- read ----------------------------------------------------------------------------------------


@contextmanager
def recording(episode: OfflineEpisode) -> Iterator[tuple[sqlite3.Connection, dict]]:
    """The episode's recording, read-only, after checking that it is this row's file."""
    connection = None
    try:
        partial, final, _ = _files(get_settings().data_dir / "recordings", episode.id)
        location = final if final.is_file() else partial if partial.is_file() else None
        if location is None:
            raise unavailable()
        connection = sqlite3.connect(location.as_uri() + "?mode=ro", uri=True, timeout=1)
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("SELECT id, evaluation_id, digest, steps, manifest_json FROM episode").fetchone()
        if row is None or tuple(row[:4]) != (episode.id, episode.evaluation_id, episode.digest, episode.steps):
            raise ValueError("recording does not match its episode")
        yield connection, json.loads(row[4])
    except HTTPException:
        raise
    except (ValueError, TypeError, KeyError, sqlite3.Error, OSError):
        raise unavailable() from None
    finally:
        if connection is not None:
            connection.close()


def frame(connection: sqlite3.Connection, index: int) -> dict:
    """Frame `index` in the replay frame shape: the camera after action `index` (the start for 0)."""
    shown = connection.execute(
        "SELECT idx, media_type, data FROM images WHERE idx <= ? ORDER BY idx DESC LIMIT 1", (index,)
    ).fetchone()
    if shown is None:
        raise ValueError("missing image")
    step = None
    if index:
        step = connection.execute(
            "SELECT action_json, reward, success, policy_ms FROM frames WHERE idx = ?", (index,)
        ).fetchone()
        if step is None:
            raise ValueError("missing step")
    return {
        "index": index,
        # The replay field keeps its name; image_media_type says whether these bytes are PNG or JPEG.
        "image_png_base64": base64.b64encode(shown[2]).decode(),
        "action": json.loads(step[0]) if step else None,
        "reward": step[1] if step else None,
        "success": None if not step or step[2] is None else bool(step[2]),
        "policy_ms": step[3] if step else None,
        "image_media_type": shown[1],
        # The step whose camera image this is: an episode may store fewer images than steps.
        "image_index": shown[0],
    }
