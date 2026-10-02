#!/usr/bin/env python3
"""Import simulation episodes recorded outside Convoy's hosted runner as an offline evaluation.

Generic: any simulator or policy can write the replay format below; nothing is signed or verified
against a release, and Convoy shows the result as "Offline sim". Standard library only.

Directory layout (the replay format: the shapes `GET …/replay` and `…/replay/frames/{i}` return)::

    DIRECTORY/
      evaluation.json         {"name", "task", "config_label", "policy_label"} (flags override)
      <episode>/              one directory per episode, imported in name order
        replay.json           {"steps", "seed", "outcome", "metrics", "action_labels", "skill",
                               "planner_ms", "wall_seconds", "sim_seconds"} (the first three required)
        frames/<index>.json   index 0..steps: {"index", "image_png_base64", "action", "reward",
                               "success", "policy_ms"}

Frame 0 is the camera before the first action (no action); frame k is the camera after action k.
`image_png_base64` is a base64 PNG or JPEG of at most 320 pixels a side. Instead of it, an image file
`frames/<index>.png|.jpg|.jpeg` beside the JSON may hold the frame; a frame with neither repeats the
previous image (frame 0 must have one). `outcome` is success, failure, timeout or safety-stop.
`metrics` maps lower_snake_case names to numbers, booleans, short text or null.

Credentials come from the environment and are never printed:

    CONVOY_SERVER                     origin, e.g. https://deployconvoy.com or http://127.0.0.1:8080
    CONVOY_API_TOKEN                  an API token (cva_…): calls the API directly (/api/v1)
    CONVOY_EMAIL, CONVOY_PASSWORD     or a sign-in: calls the website's API proxy (/api/platform)

Uploads are idempotent: a rerun of the same directory within a day reuses the evaluation and skips
stored episodes, and identical episode content is never stored twice. After 24 hours, pass
--evaluation oev_… to add to an existing evaluation. --write-example DIRECTORY writes a small set of
generic frames to try the import with.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import http.cookiejar
import ipaddress
import json
import math
import os
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import urlsplit

LABELS = ("name", "task", "config_label", "policy_label")
MANIFEST = ("seed", "outcome", "metrics", "action_labels", "skill", "planner_ms", "wall_seconds", "sim_seconds")
FRAME = ("index", "image_png_base64", "action", "reward", "success", "policy_ms", "hierarchy")
IMAGES = (".png", ".jpg", ".jpeg")
MAX_BODY = 16 * 1024 * 1024
MAX_IMAGE = 256 * 1024
MAX_WAIT_S = 600
RETRIES = 5
TIMEOUT_S = 120


class ImportFailure(Exception):
    """A refusal or invalid input, reported without credentials."""


# ---- episodes --------------------------------------------------------------------------------------


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ImportFailure(f"{path}: {error}") from None
    if not isinstance(value, dict):
        raise ImportFailure(f"{path}: expected a JSON object")
    return value


def read_episode(directory: Path) -> dict:
    """The upload body for one episode directory, in the API's episode shape."""
    manifest = read_json(directory / "replay.json")
    steps = manifest.get("steps")
    if type(steps) is not int or steps < 1:
        raise ImportFailure(f"{directory / 'replay.json'}: steps must be a positive integer")
    frames: dict[int, dict] = {}
    folder = directory / "frames"
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        frame = read_json(path)
        index = frame.get("index")
        if type(index) is not int or index in frames:
            raise ImportFailure(f"{path}: index must be an integer that no other frame uses")
        image = frame.get("image_png_base64")
        # An export of the replay endpoint repeats an earlier image under image_index: keep only the original.
        if frame.get("image_index", index) != index:
            image = None
        elif "image_png_base64" not in frame:
            image = next((base64.b64encode(file.read_bytes()).decode() for file in map(path.with_suffix, IMAGES)
                          if file.is_file()), None)
        frames[index] = {**{key: frame.get(key) for key in FRAME
                           if key != "hierarchy" or frame.get(key) is not None}, "image_png_base64": image}
    if sorted(frames) != list(range(steps + 1)):
        raise ImportFailure(f"{folder}: expected frames 0 to {steps}, found {len(frames)}")
    body = {key: manifest[key] for key in MANIFEST if manifest.get(key) is not None}
    body["frames"] = [frames[index] for index in range(steps + 1)]
    return body


def describe(body: dict) -> tuple[int, int, int]:
    """(steps, images, image bytes) of an upload, checking the limits the server enforces first."""
    images = 0
    size = 0
    for frame in body["frames"]:
        if frame["image_png_base64"] is None:
            continue
        try:
            data = base64.b64decode(frame["image_png_base64"], validate=True)
        except (binascii.Error, ValueError, TypeError):
            raise ImportFailure(f"frame {frame['index']}: image_png_base64 is not base64") from None
        if len(data) > MAX_IMAGE:
            raise ImportFailure(f"frame {frame['index']}: image exceeds {MAX_IMAGE} bytes; use a smaller frame or JPEG")
        images += 1
        size += len(data)
    return len(body["frames"]) - 1, images, size


def encode(body: dict) -> bytes:
    data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > MAX_BODY:
        raise ImportFailure(
            f"episode is {len(data)} bytes as JSON, over the {MAX_BODY} byte upload limit: use JPEG frames, "
            "smaller frames or fewer images (a frame without an image repeats the previous one)"
        )
    return data


# ---- transport -------------------------------------------------------------------------------------


class Client:
    """The API directly with an API token, or the website's API proxy with a sign-in session."""

    def __init__(self, server: str, token: str | None, email: str | None, password: str | None):
        self.server = server
        self.jar = http.cookiejar.CookieJar()
        handlers: list = [urllib.request.HTTPCookieProcessor(self.jar)]
        self.opener = urllib.request.build_opener(*handlers)
        self.headers = {"Accept": "application/json", "X-Convoy-Client": "web"}
        self.session = token is None
        if token is not None:
            self.prefix = "/api/v1/"
            self.headers["Authorization"] = f"Bearer {token}"
        else:
            if not email or not password:
                raise ImportFailure("set CONVOY_API_TOKEN, or CONVOY_EMAIL and CONVOY_PASSWORD")
            self.prefix = "/api/platform/"
            self.headers["Origin"] = server
            self.call("POST", "auth/login", json.dumps({"email": email, "password": password}).encode(), key=None)

    def close(self) -> None:
        if self.session:
            try:
                self.call("POST", "auth/logout", b"{}", key=None)
            except ImportFailure:
                pass

    def call(self, method: str, path: str, body: bytes | None = None, *, key: str | None = None) -> tuple[int, dict]:
        headers = dict(self.headers)
        if body is not None:
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Idempotency-Key"] = key
        for attempt in range(RETRIES + 1):
            request = urllib.request.Request(self.server + self.prefix + path, data=body, method=method, headers=headers)
            try:
                with self.opener.open(request, timeout=TIMEOUT_S) as response:
                    return response.status, json.loads(response.read() or b"{}")
            except urllib.error.HTTPError as error:
                message = _error(error)
                retry = error.headers.get("Retry-After", "")
                if error.code == 429 and retry.isdigit() and int(retry) <= MAX_WAIT_S and attempt < RETRIES:
                    print(f"  write budget used: waiting {retry} s", flush=True)
                    time.sleep(int(retry))
                    continue
                if error.code in (502, 503, 504) and attempt < RETRIES:
                    time.sleep(min(30, 2**attempt))
                    continue
                raise ImportFailure(f"{method} {path}: {error.code} {message}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                if attempt < RETRIES:
                    time.sleep(min(30, 2**attempt))  # the same idempotency key makes the retry safe
                    continue
                reason = getattr(error, "reason", error)
                raise ImportFailure(f"{method} {path}: {reason}") from None
        raise AssertionError("unreachable")


def _error(error: urllib.error.HTTPError) -> str:
    try:
        value = json.loads(error.read(64 * 1024))
        return str(value.get("error", "request refused"))[:500]
    except (ValueError, AttributeError, OSError):
        return "request refused"


def origin(value: str) -> str:
    parts = urlsplit(value.rstrip("/"))
    try:
        loopback = ipaddress.ip_address(parts.hostname or "").is_loopback
    except ValueError:
        loopback = parts.hostname == "localhost"
    if (parts.scheme not in ("https", "http") or (parts.scheme == "http" and not loopback) or not parts.hostname
            or parts.username or parts.password or parts.path or parts.query or parts.fragment):
        raise ImportFailure("CONVOY_SERVER must be an https origin (http only for this machine), without a path")
    return f"{parts.scheme}://{parts.netloc}"


# ---- import ----------------------------------------------------------------------------------------


def run(args: argparse.Namespace) -> int:
    root = args.directory.resolve()
    labels_file = root / "evaluation.json"
    stored = read_json(labels_file) if labels_file.is_file() else {}
    labels = {key: getattr(args, key) or stored.get(key) for key in LABELS}
    episodes = sorted(path for path in root.iterdir() if (path / "replay.json").is_file())
    if not episodes:
        raise ImportFailure(f"{root}: no episode directories (each needs replay.json)")
    # Check every episode first (one in memory at a time); upload only if all are within the limits.
    digests = []
    for directory in episodes:
        body = read_episode(directory)
        steps, images, size = describe(body)
        digests.append(hashlib.sha256(encode(body)).hexdigest())
        print(f"{directory.name}: {steps} steps, {images} images, {size / 1e6:.2f} MB of frames, "
              f"seed {body.get('seed')}, {body.get('outcome')}")
    if args.dry_run:
        print(f"{len(episodes)} episodes checked; nothing uploaded (--dry-run)")
        return 0
    if not args.evaluation and not all(isinstance(labels[key], str) and labels[key].strip() for key in LABELS):
        raise ImportFailure("name, task, config_label and policy_label are required (evaluation.json or flags)")
    server = origin(args.server or os.environ.get("CONVOY_SERVER", ""))
    client = Client(server, os.environ.get("CONVOY_API_TOKEN") or None, os.environ.get("CONVOY_EMAIL"),
                    os.environ.get("CONVOY_PASSWORD"))
    try:
        # One namespace per import: a rerun replays its own writes instead of repeating them.
        namespace = hashlib.sha256(json.dumps([labels, digests]).encode()).hexdigest()[:32]
        evaluation = args.evaluation
        if evaluation is None:
            _, created = client.call("POST", "offline-evaluations", json.dumps(labels).encode(), key=f"import-{namespace}")
            evaluation = created["id"]
        print(f"offline evaluation {evaluation} on {server}")
        for directory in episodes:
            data = encode(read_episode(directory))
            digest = hashlib.sha256(data).hexdigest()
            status, episode = client.call("POST", f"offline-evaluations/{evaluation}/episodes", data,
                                          key=f"import-{namespace}-{digest[:32]}")
            note = "already stored" if status == 200 else "stored"
            print(f"  {directory.name}: {note} as {episode['id']} ({episode['stored_bytes'] / 1e6:.2f} MB stored)")
        _, final = client.call("GET", f"offline-evaluations/{evaluation}")
        summary = final["summary"]
        rate = "–" if summary["success_rate"] is None else f"{summary['success_rate'] * 100:.0f} %"
        print(f"{summary['episodes']} episodes, {summary['successes']} successes ({rate}); unsigned offline import. "
              "Show it in Configurations: Add robot → Simulator · offline.")
    finally:
        client.close()
    return 0


# ---- example ---------------------------------------------------------------------------------------


def png(width: int, height: int, pixel) -> bytes:
    rows = b"".join(b"\0" + bytes(channel for x in range(width) for channel in pixel(x, y)) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")


def write_example(target: Path, episodes: int = 3, steps: int = 24, width: int = 14) -> None:
    """Generic frames: a gradient with a square that moves with the first two action values."""
    if target.exists() and any(target.iterdir()):
        raise ImportFailure(f"{target} is not empty")
    target.mkdir(parents=True, exist_ok=True)
    (target / "evaluation.json").write_text(json.dumps({
        "name": "Example · generic frames", "task": "Generic reach", "config_label": "Example config",
        "policy_label": "Example sine policy"}, indent=2))
    labels = [f"{side}_{axis}" for side in ("l", "r") for axis in ("x", "y", "z", "rx", "ry", "rz", "grip")][:width]
    for episode in range(episodes):
        folder = target / f"episode-{episode:03d}" / "frames"
        folder.mkdir(parents=True)
        success = episode != episodes - 1
        x, y, reward_sum = 20.0, 60.0, 0.0
        for index in range(steps + 1):
            action = None if index == 0 else [round(math.sin(index / 4 + axis + episode), 3) for axis in range(width)]
            if action:
                x, y = min(150, max(10, x + 4 * action[0] + 3)), min(110, max(10, y + 4 * action[1]))
            def pixel(px, py, sx=x, sy=y, shade=episode * 40):
                inside = abs(px - sx) < 8 and abs(py - sy) < 8
                return (230, 90, 40) if inside else ((px + shade) % 256, (py * 2) % 256, 140)
            reward = None if index == 0 else round(1 - abs(150 - x) / 150, 3)
            reward_sum += reward or 0
            frame = {
                "index": index, "image_png_base64": base64.b64encode(png(160, 120, pixel)).decode(), "action": action,
                "reward": reward, "success": None if index == 0 else success and index == steps,
                "policy_ms": None if index == 0 else round(8 + 2 * math.cos(index), 2),
            }
            (folder / f"{index:04d}.json").write_text(json.dumps(frame))
        (folder.parent / "replay.json").write_text(json.dumps({
            "steps": steps, "seed": episode, "outcome": "success" if success else "timeout",
            "metrics": {"reward_sum": round(reward_sum, 3), "final_x": round(x, 1)}, "action_labels": labels,
            "wall_seconds": round(1.5 + 0.2 * episode, 2), "sim_seconds": round(steps * 0.0125, 4)}, indent=2))
    print(f"wrote {episodes} example episodes of {steps} steps to {target}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("directory", type=Path, help="episodes in the replay format (or the --write-example target)")
    parser.add_argument("--server", help="origin; default CONVOY_SERVER")
    parser.add_argument("--evaluation", help="add to this offline evaluation (oev_…) instead of creating one")
    parser.add_argument("--name")
    parser.add_argument("--task")
    parser.add_argument("--config", dest="config_label")
    parser.add_argument("--policy", dest="policy_label")
    parser.add_argument("--dry-run", action="store_true", help="check the episodes and sizes; upload nothing")
    parser.add_argument("--write-example", action="store_true", help="write example episodes to DIRECTORY and exit")
    args = parser.parse_args(argv)
    try:
        if args.write_example:
            write_example(args.directory)
            return 0
        if not args.directory.is_dir():
            raise ImportFailure(f"{args.directory} is not a directory")
        return run(args)
    except ImportFailure as error:
        print(f"import_offline_eval: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
