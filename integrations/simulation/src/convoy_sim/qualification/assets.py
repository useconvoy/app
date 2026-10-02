"""Bounded content-addressed robot bundles, with no network fetch or executable plugins."""
from __future__ import annotations

import hashlib
import io
import stat
import zipfile
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree

MAX_BYTES = 100 * 1024 * 1024
MAX_FILES = 512


def read_asset(path: Path, expected_digest: str) -> bytes:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError("asset must be a regular file of at most 100 MiB")
    with path.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES or hashlib.sha256(payload).hexdigest() != expected_digest:
        raise ValueError("asset digest does not match the pinned profile")
    return payload


def safe_name(name: str) -> str:
    path = PurePosixPath(name)
    if not name or name.startswith("/") or "\\" in name or ":" in name or ".." in path.parts:
        raise ValueError("bundle references must stay inside the bundle")
    return str(path)


def mujoco_files(payload: bytes, format_name: str) -> tuple[str, dict[str, bytes]]:
    if format_name == "mjcf":
        files = {"model.xml": payload}
    elif format_name == "bundle":
        files = {}
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(e.file_size for e in entries) > MAX_BYTES:
                raise ValueError("robot bundle exceeds the file or decompressed size limit")
            for entry in entries:
                name = safe_name(entry.filename)
                if entry.is_dir():
                    continue
                if name in files or stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValueError("robot bundle contains duplicate names or symbolic links")
                files[name] = archive.read(entry)
    else:
        raise ValueError("MuJoCo requires MJCF or a bundle with model.xml")
    if "model.xml" not in files:
        raise ValueError("robot bundle requires model.xml at its root")
    # MuJoCo's compiler can resolve files outside its virtual filesystem. Refuse every external
    # reference before native parsing. This also inspects include files and rejects custom plugins.
    for name, contents in files.items():
        if not name.lower().endswith(".xml"):
            continue
        xml = contents.decode("utf-8")
        if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
            raise ValueError("XML entity declarations are not supported")
        root = ElementTree.fromstring(xml)
        for node in root.iter():
            if node.tag in {"plugin", "extension"}:
                raise ValueError("native plugins require a separately qualified runner")
            if node.tag == "compiler" and any(node.get(key) for key in ("assetdir", "meshdir", "texturedir")):
                raise ValueError("use explicit bundle-relative asset paths, not compiler directories")
            reference = node.get("file")
            if reference:
                resolved = safe_name(reference)
                if resolved not in files:
                    raise ValueError(f"bundle is missing referenced asset: {resolved}")
                if node.tag == "include" and not resolved.lower().endswith(".xml"):
                    raise ValueError("included XML must use an .xml extension")
    return files["model.xml"].decode("utf-8"), files
