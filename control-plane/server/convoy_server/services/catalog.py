"""Immutable catalog objects: eval sets, build recipes, runtime artifacts, releases (release_spec_v1), plans."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from convoy_agent import gguf
from convoy_agent.evaluator import EVALUATOR_VERSION, METRICS, validate_gates
from convoy_agent.runtime_args import DEFAULT_CONFIG, argv_for, canonical_config
from convoy_agent.scoring import MATCH_KINDS, SCORER_VERSION, validate_case
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..config import Settings
from ..hardware import PROFILES, plan_budget, profile
from ..ids import iso, new_id, utcnow
from ..models import BuildRecipe, Device, EvalSet, Plan, Release, RuntimeArtifact
from ..modelsource import FixtureSource, HFSource, ModelSourceError, Resolved, SuppliedSource, blob_path

LLAMA_CPP_TAG = "v0.4.0"
LLAMA_CPP_COMMIT = "5266f24da75dc449bd56cbed7addb9c8e4a6a73e"
SPEC_VERSION = 1
QWEN_BASELINE = {
    "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
    "revision": "91cad51170dc346986eccefdc2dd33a9da36ead9",
    "file": "qwen2.5-1.5b-instruct-q4_k_m.gguf",
    "size": 1117320736,
    "sha256": "6a1a2eb6d15622bf3c96857206351ba97e1af16c30d7a74ee38970e434e9407e",
    "provenance": "supplied by supervisor 2026-09-12; huggingface.co unreachable from the build sandbox; pending device verification",
}
MAX_TEMPLATE_BYTES = 64 * 1024
MAX_ARCHIVE_MEMBERS = 512


class CatalogError(ValueError):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(obj: Any) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


# ---------- eval sets ----------
def parse_jsonl(text: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise CatalogError(f"line {i}: invalid JSON ({e.msg})") from e
        if not isinstance(obj, dict):
            raise CatalogError(f"line {i}: each line must be an object")
        cases.append(obj)
    return cases


def _finite_errors(obj: Any, where: str, depth: int = 0) -> list[str]:
    """R18 follow-up: JSONL can smuggle Infinity/NaN and deep nesting; reject before persistence."""
    import math as _m

    if depth > 8:
        return [f"{where}: nesting too deep"]
    if isinstance(obj, float) and (_m.isnan(obj) or _m.isinf(obj)):
        return [f"{where}: non-finite number"]
    if isinstance(obj, dict):
        if len(obj) > 64:
            return [f"{where}: too many keys"]
        out: list[str] = []
        for k, v in obj.items():
            out += _finite_errors(v, f"{where}.{k}", depth + 1)
        return out
    if isinstance(obj, list):
        if len(obj) > 256:
            return [f"{where}: list too long"]
        out = []
        for v in obj:
            out += _finite_errors(v, where, depth + 1)
        return out
    if isinstance(obj, str) and len(obj) > 16384:
        return [f"{where}: string too long"]
    return []


def create_eval_set(
    db: DbSession,
    *,
    name: str,
    version: str,
    cases: list[dict[str, Any]],
    scorer: dict[str, Any] | None,
    description: str,
    created_by: str | None,
) -> EvalSet:
    if not cases:
        raise CatalogError("eval set has no cases")
    if len(cases) > 5000:
        raise CatalogError("eval set too large (max 5000 cases)")
    errs: list[str] = []
    ids: set[str] = set()
    for c in cases:
        errs += validate_case(c)
        errs += _finite_errors(c, f"case {c.get('id')}")
        if c.get("id") in ids:
            errs.append(f"duplicate case id {c.get('id')}")
        ids.add(c.get("id"))
    if errs:
        raise CatalogError("; ".join(errs[:10]))
    if hasattr(scorer, "model_dump"):
        scorer = scorer.model_dump()  # R23: strict model from the router
    sc = {
        "version": SCORER_VERSION,
        "kinds": list(MATCH_KINDS),
        "ordering": "fixed",
        "warmup": 1,
        **(scorer or {}),
    }
    if sc.get("version") != SCORER_VERSION:
        raise CatalogError(f"scorer version must be {SCORER_VERSION}")
    digest = sha({"cases": cases, "scorer": sc})
    if db.scalar(select(EvalSet).where(EvalSet.name == name, EvalSet.version == version)):
        raise CatalogError("eval set name/version already exists; eval sets are immutable", 409)
    row = EvalSet(
        id=new_id("evs"),
        name=name,
        version=version,
        digest=digest,
        cases=cases,
        scorer=sc,
        description=description,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def eval_set_out(e: EvalSet, brief: bool = False) -> dict[str, Any]:
    out = {
        "id": e.id,
        "name": e.name,
        "version": e.version,
        "digest": e.digest,
        "scorer": e.scorer,
        "description": e.description,
        "case_count": len(e.cases),
        "created_at": iso(e.created_at),
        "created_by": e.created_by,
    }
    if not brief:
        out["cases"] = e.cases
    return out


# ---------- build recipes ----------
DEFAULT_CMAKE = [
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
]  # R22: never fetch the mutable prebuilt WebUI
CUDA_TARGET = {
    "arch": "aarch64",
    "os": "linux",
    "backend": "cuda",
    "compute_capability": "8.7",
    "jetpack": "6.2.3",
    "l4t": "36.5.2",
    "cuda": "12.6",
}
# Explicit CUDA tracks a recipe can pin. Same commit, same CMake flags, same SM 8.7 and CUDA 12.6 major.minor;
# they differ only in the L4T release the receipt must prove. `jp623` is the original supervisor-verified
# pin (kept as CUDA_TARGET for existing recipes). `l4t3647` is the board-observed tuple of the project's
# Jetson Orin Nano Developer Kit Super (L4T 36.4.7, CUDA 12.6.11): it is named by the observed L4T
# release because no JetPack version is established for it (the nvidia-jetpack metapackage is absent and
# NVIDIA's JetPack 6.2.2 page lists Jetson Linux 36.5, not 36.4.7), so the tuple carries no `jetpack`
# key. Candidate track, physical qualification pending. A runtime artifact built on one track never
# registers under the other.
CUDA_TRACKS: dict[str, dict[str, str]] = {
    "jp623": dict(CUDA_TARGET),
    "l4t3647": {**{k: v for k, v in CUDA_TARGET.items() if k != "jetpack"}, "l4t": "36.4.7"},
}
DEFAULT_TRACK = "jp623"


def cuda_target(track: str | None) -> dict[str, str]:
    """The target tuple for a named track (None -> the original pin)."""
    if track is None:
        return dict(CUDA_TARGET)
    if track not in CUDA_TRACKS:
        raise CatalogError(f"unknown CUDA track {track!r}; known: {', '.join(sorted(CUDA_TRACKS))}")
    return dict(CUDA_TRACKS[track])


def track_for_target(target: dict[str, Any] | None) -> str | None:
    """Name of the track whose tuple equals `target` (None when it matches no track)."""
    for name, t in CUDA_TRACKS.items():
        if target and all(str(target.get(k)) == v for k, v in t.items()) and set(target) == set(t):
            return name
    return None


def create_recipe(
    db: DbSession,
    *,
    name: str,
    commit: str,
    tag: str | None,
    cmake_flags: list[str],
    target: dict[str, Any],
    backend: str,
    created_by: str | None,
) -> BuildRecipe:
    if len(commit) != 40 or any(c not in "0123456789abcdef" for c in commit):
        raise CatalogError("commit must be a 40-hex sha")
    if backend not in ("cuda", "cpu", "simulated"):
        raise CatalogError("backend must be cuda|cpu|simulated")
    for f in cmake_flags:
        if not isinstance(f, str) or not f.startswith("-D") or any(ch in f for ch in " ;&|`$"):
            raise CatalogError(f"unsafe cmake flag {f!r}")
    digest = sha(
        {
            "runtime": "llama.cpp",
            "commit": commit,
            "cmake": sorted(cmake_flags),
            "target": target,
            "backend": backend,
        }
    )
    existing = db.scalar(select(BuildRecipe).where(BuildRecipe.digest == digest))
    if existing:
        return existing
    row = BuildRecipe(
        id=new_id("rcp"),
        name=name,
        commit=commit,
        tag=tag,
        cmake_flags=cmake_flags,
        target=target,
        backend=backend,
        digest=digest,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def recipe_out(r: BuildRecipe) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.name,
        "runtime_name": r.runtime_name,
        "source_repo": r.source_repo,
        "commit": r.commit,
        "tag": r.tag,
        "cmake_flags": r.cmake_flags,
        "target": r.target,
        "track": track_for_target(r.target) if r.backend == "cuda" else None,
        "backend": r.backend,
        "digest": r.digest,
        "created_at": iso(r.created_at),
    }


# ---------- runtime artifacts ----------
REQUIRED_RECEIPT = ("archive_sha256", "archive_size", "files", "provenance")


def register_artifact(
    db: DbSession,
    settings: Settings,
    *,
    recipe: BuildRecipe,
    receipt: dict[str, Any],
    scope: str,
    storage: str,
    created_by: str | None,
) -> RuntimeArtifact:
    if hasattr(receipt, "model_dump"):
        receipt = receipt.model_dump(exclude_none=True)
    for k in REQUIRED_RECEIPT:
        if k not in receipt:
            raise CatalogError(f"receipt missing {k}")
    a_sha = str(receipt["archive_sha256"]).lower()
    if len(a_sha) != 64:
        raise CatalogError("archive_sha256 must be 64 hex")
    files = receipt["files"]
    if not isinstance(files, list) or not files or len(files) > MAX_ARCHIVE_MEMBERS:
        raise CatalogError("files must be a non-empty list (<=512)")
    names = set()
    for f in files:
        if (
            not isinstance(f, dict)
            or not isinstance(f.get("path"), str)
            or len(str(f.get("sha256", ""))) != 64
        ):
            raise CatalogError("each file needs path and sha256")
        p = normalize_member(f["path"])
        if p in names:
            raise CatalogError(f"duplicate receipt path {p!r}")
        f["path"] = p
        names.add(p)
    if not any(n.endswith("llama-server") or n.endswith("llama-server.sim") for n in names):
        raise CatalogError("archive must contain the llama-server executable")
    if scope != "fleet" and not (scope.startswith("device:") and db.get(Device, scope[7:])):
        raise CatalogError("scope must be 'fleet' or 'device:<existing id>'")
    if storage not in ("device", "server"):
        raise CatalogError("storage must be device|server")
    _check_receipt_matches_recipe(recipe, receipt)
    if storage == "server":
        p = blob_path(settings, a_sha, "runtime")
        if not p.exists():
            raise CatalogError("archive bytes not uploaded yet; upload first, then register", 409)
        if p.stat().st_size != int(receipt["archive_size"]):
            raise CatalogError("uploaded archive size does not match the receipt", 409)
        inspect_archive(p, files)  # R26: exact file set + hashes must agree with the receipt
    if db.scalar(
        select(RuntimeArtifact).where(RuntimeArtifact.archive_sha256 == a_sha, RuntimeArtifact.scope == scope)
    ):
        raise CatalogError("artifact already registered for this scope", 409)
    prov = receipt["provenance"] if isinstance(receipt["provenance"], dict) else {}
    row = RuntimeArtifact(
        id=new_id("art"), recipe_id=recipe.id, archive_sha256=a_sha, archive_size=int(receipt["archive_size"]), files=files, provenance=prov,
        scope=scope, storage=storage, receipt=receipt, verified="archive_verified" if storage == "server" else "receipt_only", created_by=created_by,
    )  # fmt: skip
    db.add(row)
    db.flush()
    return row


def _norm_flag(f: str) -> str:
    """`-DNAME:TYPE=VALUE` and `-DNAME=VALUE` are the same cache entry."""
    if f.startswith("-D") and "=" in f:
        name, _, value = f[2:].partition("=")
        return f"-D{name.split(':', 1)[0]}={value}"
    return f


def _l4t_from_release_line(line: str) -> str | None:
    """`# R36 (release), REVISION: 5.2, ...` -> `36.5.2`."""
    m = re.search(r"R(\d+)\s*\(release\),\s*REVISION:\s*(\d+(?:\.\d+)*)", line or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def _check_receipt_matches_recipe(recipe: BuildRecipe, receipt: dict[str, Any]) -> None:
    """R71: a binary acquires the recipe's compatibility identity only when its receipt provenance proves
    the same source commit, the same cmake cache entries and (for CUDA recipes) the pinned toolkit tuple.
    A different-toolkit build must be registered under its own recipe, never under the pinned one."""
    prov = receipt.get("provenance") if isinstance(receipt.get("provenance"), dict) else {}
    if recipe.backend == "simulated":
        return
    for k in ("commit", "cmake_flags", "cuda_version", "l4t_release"):
        if k not in prov:
            raise CatalogError(f"receipt provenance must carry {k} for a {recipe.backend} recipe", 409)
    if str(prov["commit"]).lower() != recipe.commit:
        raise CatalogError(f"receipt was built from {prov['commit']}, recipe pins {recipe.commit}", 409)
    want = sorted(_norm_flag(str(f)) for f in (recipe.cmake_flags or []))
    got = sorted(_norm_flag(str(f)) for f in (prov.get("cmake_flags") or []))
    if want != got:
        raise CatalogError(f"receipt cmake flags {got} differ from the recipe's {want}", 409)
    if recipe.backend == "cuda":
        target = recipe.target or {}
        cuda = ".".join(str(prov["cuda_version"]).split(".")[:2])
        if target.get("cuda") and cuda != str(target["cuda"]):
            raise CatalogError(
                f"receipt CUDA {cuda} does not match the recipe's pinned CUDA {target['cuda']}", 409
            )
        l4t = _l4t_from_release_line(str(prov["l4t_release"])) or str(prov["l4t_release"])
        if target.get("l4t") and l4t != str(target["l4t"]):
            raise CatalogError(
                f"receipt L4T {l4t} does not match the recipe's pinned L4T {target['l4t']}", 409
            )


def verify_artifacts(db: DbSession, settings: Settings) -> dict[str, Any]:
    """R64: after a restore onto a replacement host, every server-stored runtime artifact referenced by
    the catalog must exist in the data volume with the recorded sha256 before dispatch resumes."""
    out: dict[str, Any] = {"checked": 0, "missing": [], "mismatched": [], "device_stored": []}
    for a in db.scalars(select(RuntimeArtifact)):
        if a.storage != "server":
            out["device_stored"].append(a.id)
            continue
        out["checked"] += 1
        p = blob_path(settings, a.archive_sha256, "runtime")
        if not p.exists():
            out["missing"].append({"artifact_id": a.id, "path": str(p)})
            continue
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        if h.hexdigest() != a.archive_sha256 or p.stat().st_size != a.archive_size:
            out["mismatched"].append({"artifact_id": a.id, "path": str(p), "sha256": h.hexdigest()})
    out["ok"] = not out["missing"] and not out["mismatched"]
    return out


def artifact_out(a: RuntimeArtifact) -> dict[str, Any]:
    return {
        "id": a.id,
        "recipe_id": a.recipe_id,
        "archive_sha256": a.archive_sha256,
        "archive_size": a.archive_size,
        "files": a.files,
        "provenance": a.provenance,
        "scope": a.scope,
        "storage": a.storage,
        "verified": a.verified,
        "created_at": iso(a.created_at),
        "created_by": a.created_by,
    }


class UploadSink:
    """Incremental, bounded archive receiver (R19). write() hashes and appends to a temp file under the
    artifact store; finish_and_inspect() validates the tar and publishes it by rename + fsync; abort()
    removes the partial. Nothing is published before inspection succeeds."""

    def __init__(self, settings: Settings, max_bytes: int, quota_bytes: int):
        self.dir = settings.artifacts_dir / "runtime"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes
        self.quota = quota_bytes
        self.used = sum(
            f.stat().st_size for f in self.dir.iterdir() if f.is_file() and not f.name.startswith(".")
        )
        self.tmp = self.dir / f".upload-{new_id('u', 8)}.part"
        self.h = hashlib.sha256()
        self.n = 0
        self.f = open(self.tmp, "wb")

    def write(self, chunk: bytes) -> None:
        self.n += len(chunk)
        if self.n > self.max_bytes:
            raise CatalogError(f"archive exceeds max upload size {self.max_bytes}", 413)
        if self.used + self.n > self.quota:
            raise CatalogError("artifact store quota exceeded", 507)
        self.h.update(chunk)
        self.f.write(chunk)

    def finish_and_inspect(self) -> dict[str, Any]:
        self.f.flush()
        os.fsync(self.f.fileno())
        self.f.close()
        if self.n == 0:
            raise CatalogError("empty upload", 400)
        members = inspect_archive(self.tmp)  # raises CatalogError / tarfile errors before publication
        digest = self.h.hexdigest()
        final = self.dir / digest
        if final.exists():
            self.tmp.unlink()
        else:
            os.replace(self.tmp, final)
        fd = os.open(str(self.dir), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return {"archive_sha256": digest, "archive_size": self.n, "members": members}

    def abort(self) -> None:
        try:
            if not self.f.closed:
                self.f.close()
        finally:
            if self.tmp.exists():
                self.tmp.unlink()


def normalize_member(name: str) -> str:
    """Canonical member path: no leading './', no '.' segments, no empty segments, no traversal."""
    if name.startswith("/") or "\\" in name:
        raise CatalogError(f"unsafe archive member {name!r}")
    parts = [seg for seg in name.split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        raise CatalogError(f"unsafe archive member {name!r}")
    return "/".join(parts)


def inspect_archive(path: Path, expected: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Bounded tar inspection (R26): regular files only, canonical paths, no duplicates/aliases, member
    count and size limits; when a receipt is given, the file set and every hash must agree exactly."""
    out = []
    seen: set[str] = set()
    total = 0
    with tarfile.open(path, "r:*") as tf:
        for i, m in enumerate(tf):
            if i >= MAX_ARCHIVE_MEMBERS:
                raise CatalogError("archive has too many members")
            if m.isdir():
                continue
            if not m.isreg():
                raise CatalogError(
                    f"non-regular archive member {m.name!r} (symlinks/devices are not allowed)"
                )
            name = normalize_member(m.name)
            if name in seen:
                raise CatalogError(f"duplicate/aliased archive member {m.name!r}")
            seen.add(name)
            total += m.size
            if total > 4 << 30:
                raise CatalogError("archive too large")
            src = tf.extractfile(m)
            h = hashlib.sha256()
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk)
            out.append({"path": name, "size": m.size, "sha256": h.hexdigest()})
    if expected is not None:
        exp = {normalize_member(f["path"]): f for f in expected}
        got = {f["path"]: f for f in out}
        if set(exp) != set(got):
            raise CatalogError(
                f"archive members do not match the receipt exactly (missing={sorted(set(exp) - set(got))[:5]}, unlisted={sorted(set(got) - set(exp))[:5]})"
            )
        for k, f in exp.items():
            if got[k]["sha256"] != str(f.get("sha256", "")).lower() or (
                f.get("size") is not None and int(f["size"]) != got[k]["size"]
            ):
                raise CatalogError(f"archive member {k} does not match the receipt hash/size")
    return out


# ---------- releases ----------
def resolve_model(db: DbSession, settings: Settings, spec: dict[str, Any]) -> Resolved:
    source = spec.get("source", "hf")
    repo, revision = spec.get("repo", ""), spec.get("revision", "")
    if source == "fixture":
        if not settings.simulator:
            raise CatalogError("fixture source requires simulator mode", 409)
        return FixtureSource(settings, db).resolve(repo, revision, spec.get("files", []))
    if source == "supplied":
        return SuppliedSource(settings).resolve(repo, revision, spec.get("supplied_files", []))
    if source == "hf":
        return HFSource(settings).resolve(repo, revision, spec.get("files", []))
    raise CatalogError(f"unknown model source {source}")


def _template_from(data: dict[str, Any]) -> dict[str, Any] | None:
    t = data.get("template")
    if not t:
        return None
    text = t.get("text", "")
    if not isinstance(text, str) or not text or len(text.encode()) > MAX_TEMPLATE_BYTES:
        raise CatalogError("template text must be non-empty and <= 64 KiB")
    return {
        "name": str(t.get("name", "reviewed"))[:64],
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "text": text,
        "reviewed_by": t.get("reviewed_by"),
    }


KV_COUNT_KEYS = ("n_layers", "n_kv_heads", "head_dim")


def counts_contradict(a: dict[str, Any] | None, b: dict[str, Any] | None) -> list[str]:
    """Keys of kv_estimate_inputs on which two metadata views disagree (both present, different)."""
    ka = ((a or {}).get("kv_estimate_inputs") or {}) if a else {}
    kb = ((b or {}).get("kv_estimate_inputs") or {}) if b else {}
    out = []
    for k in KV_COUNT_KEYS + ("n_embd", "n_vocab"):
        va, vb = ka.get(k), kb.get(k)
        if va is not None and vb is not None and str(va) != str(vb):
            out.append(f"{k}: {va} vs {vb}")
    aa, ab = (a or {}).get("architecture"), (b or {}).get("architecture")
    if aa and ab and aa != ab:
        out.append(f"architecture: {aa} vs {ab}")
    return out


def derive_gguf_metadata(
    settings: Settings, resolved: Resolved, supplied: dict[str, Any] | None
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """The metadata a release records for a model file, with its provenance.
    fixture: read from the stored bytes (byte_verified). hf/supplied: the header is inspected by bounded
    range fetch from the pinned commit URL (advisory, not hash-verified). An operator-supplied `gguf`
    is accepted only when nothing contradicts it: against an inspected header a contradiction is a
    422; with no header available it is kept, labelled operator_supplied. With neither, the release
    records no counts and admission is pending byte inspection on the device (never a dead end,
    never a fabricated budget)."""
    f = resolved.files[0]
    if resolved.source == "fixture":
        try:
            meta = gguf.qualification(gguf.read_metadata(str(blob_path(settings, f.sha256))))
        except (gguf.GGUFError, OSError) as e:
            raise CatalogError(f"fixture GGUF unreadable: {e}") from e
        return meta, {"method": "blob", "byte_verified": True, "sha256": f.sha256}
    from ..modelsource import inspect_remote_gguf

    try:
        ins = inspect_remote_gguf(f.url, f.size)
        meta, prov = ins["qualification"], ins["provenance"]
    except ModelSourceError as e:
        meta, prov = (
            None,
            {"method": "unavailable", "byte_verified": False, "reason": str(e)[:300], "url": f.url},
        )
    if supplied:
        if meta:
            diffs = counts_contradict(supplied, meta)
            if diffs:
                raise CatalogError(
                    f"supplied GGUF metadata contradicts the file header at the pinned commit: {'; '.join(diffs)}",
                    422,
                )
            prov = {**prov, "operator_supplied_agrees": True}
        else:
            meta, prov = (
                supplied,
                {
                    **prov,
                    "method": "operator_supplied",
                    "note": "operator-supplied counts, not verified against any bytes; the device derives the authoritative counts at staging",
                },
            )
    if meta is None:
        prov = {
            **prov,
            "admission": "pending_byte_inspection",
            "note": "no header could be inspected; the device derives the counts from the sha256-verified file at staging before any memory admission",
        }
    return meta, prov


@dataclass
class PreparedRelease:
    """Everything a release needs that was gathered OUTSIDE the write transaction: the resolved model,
    the (possibly remote) header inspection, validated config/budget and the ids plus content digests of
    the catalog rows it references. `persist_release` re-reads those rows inside the transaction and
    refuses to write if any of them is gone or differs from what was validated here."""

    data: dict[str, Any]
    resolved: Resolved
    gguf_meta: dict[str, Any] | None
    gguf_prov: dict[str, Any]
    qual: str
    template: dict[str, Any] | None
    profile_id: str
    cfg: dict[str, Any]
    budget: dict[str, Any]
    recipe_id: str
    recipe_digest: str
    artifact_id: str | None
    artifact_sha256: str | None
    eval_set_id: str | None
    eval_set_digest: str | None


def _release_refs(
    db: DbSession, data: dict[str, Any], profile_id: str
) -> tuple[BuildRecipe, RuntimeArtifact | None, EvalSet | None]:
    """Resolve and cross-check the recipe, artifact and eval set a release binds. Called once during
    preparation (fast failure) and again inside the write transaction (authority)."""
    recipe = db.get(BuildRecipe, data["recipe_id"]) if data.get("recipe_id") else None
    artifact = (
        db.get(RuntimeArtifact, data["runtime_artifact_id"]) if data.get("runtime_artifact_id") else None
    )
    if data.get("runtime_artifact_id") and not artifact:
        raise CatalogError("runtime artifact not found")
    if artifact and (not recipe or artifact.recipe_id != recipe.id):
        raise CatalogError("runtime artifact must belong to the given recipe")
    if not recipe:
        raise CatalogError("recipe_id is required")
    if profile_id == "simulated-host" and recipe.backend != "simulated":
        raise CatalogError("simulated releases need a simulated recipe")
    if profile_id != "simulated-host" and recipe.backend == "simulated":
        raise CatalogError("a simulated recipe cannot back a physical release")
    es = db.get(EvalSet, data["eval_set_id"]) if data.get("eval_set_id") else None
    if data.get("eval_set_id") and not es:
        raise CatalogError("eval set not found")
    return recipe, artifact, es


def prepare_release(db: DbSession, settings: Settings, data: dict[str, Any]) -> PreparedRelease:
    """Phase 1 of release creation, to run OUTSIDE any write transaction: model resolution, the remote
    GGUF header inspection (a stalled model host must never hold the database writer), the fixture blob
    read, template/config/budget validation and a first look at the referenced catalog rows."""
    try:
        resolved = resolve_model(db, settings, data["model"])
    except ModelSourceError as e:
        raise CatalogError(str(e), 422) from e
    gguf_meta, gguf_prov = derive_gguf_metadata(settings, resolved, data["model"].get("gguf"))
    qual = (gguf_meta or {}).get("status") or "needs_qualification"
    template = _template_from(data)
    if qual == "recognized" and not (gguf_meta or {}).get("has_chat_template") and not template:
        qual = "needs_qualification"
    profile_id = data.get("profile_id") or (
        "simulated-host" if resolved.source == "fixture" else "jetson-orin-nano-8gb"
    )
    if profile_id not in PROFILES:
        raise CatalogError("unknown hardware profile")
    try:
        from ..schemas_catalog import BudgetIn, RuntimeConfigIn

        RuntimeConfigIn(**(data.get("config") or {}))
        budget_in = BudgetIn(**(data.get("budget") or {})).model_dump(exclude_none=True)
        cfg = canonical_config(data.get("config") or {})
    except Exception as e:
        raise CatalogError(f"invalid release config/budget: {str(e).splitlines()[0][:200]}") from e
    recipe, artifact, es = _release_refs(db, data, profile_id)
    return PreparedRelease(
        data=data,
        resolved=resolved,
        gguf_meta=gguf_meta,
        gguf_prov=gguf_prov,
        qual=qual,
        template=template,
        profile_id=profile_id,
        cfg=cfg,
        budget=budget_in,
        recipe_id=recipe.id,
        recipe_digest=recipe.digest,
        artifact_id=artifact.id if artifact else None,
        artifact_sha256=artifact.archive_sha256 if artifact else None,
        eval_set_id=es.id if es else None,
        eval_set_digest=es.digest if es else None,
    )


def persist_release(
    db: DbSession, settings: Settings, prep: PreparedRelease, created_by: str | None
) -> Release:
    """Phase 2, inside a brief write transaction: re-read the referenced rows and check they are the
    ones that were validated (exist, same content digests, same cross-references), check uniqueness
    (content digest and name/version) and persist. No network, no blob reads."""
    data, resolved, f = prep.data, prep.resolved, prep.resolved.files[0]
    recipe, artifact, es = _release_refs(db, data, prep.profile_id)
    if recipe.id != prep.recipe_id or recipe.digest != prep.recipe_digest:
        raise CatalogError("recipe changed while the release was being prepared; retry", 409)
    if (artifact.id if artifact else None) != prep.artifact_id or (
        artifact.archive_sha256 if artifact else None
    ) != prep.artifact_sha256:
        raise CatalogError("runtime artifact changed while the release was being prepared; retry", 409)
    if (es.id if es else None) != prep.eval_set_id or (es.digest if es else None) != prep.eval_set_digest:
        raise CatalogError("eval set changed while the release was being prepared; retry", 409)
    gguf_meta, gguf_prov, template, cfg = prep.gguf_meta, prep.gguf_prov, prep.template, prep.cfg
    spec = {
        "schema_version": SPEC_VERSION,
        "model": {
            "source": resolved.source,
            "repo": resolved.repo,
            "revision": resolved.revision,
            "commit": resolved.commit,
            "file": {"path": f.path, "size": f.size, "sha256": f.sha256, "url": f.url, "auth": f.auth},
            "total_bytes": f.size,
            "gguf": gguf_meta or {},
            "gguf_provenance": gguf_prov,
            "verified": resolved.verified,
        },  # fmt: skip
        "template": template,
        "runtime": {
            "name": "llama.cpp",
            "commit": recipe.commit,
            "tag": recipe.tag,
            "recipe_digest": recipe.digest,
            "backend": recipe.backend,
            "artifact_sha256": artifact.archive_sha256 if artifact else None,
            "artifact_files": artifact.files if artifact else None,
        },  # fmt: skip
        "config": cfg,
        "budget": prep.budget,
        "platform": {"profile_id": prep.profile_id, "target": recipe.target},
        "eval_set_digest": es.digest if es else None,
    }
    digest = sha(spec)
    if db.scalar(select(Release).where(Release.digest == digest)):
        raise CatalogError("an identical release already exists", 409)
    if db.scalar(select(Release).where(Release.name == data["name"], Release.version == data["version"])):
        raise CatalogError("release name/version already exists; releases are immutable", 409)
    row = Release(
        id=new_id("rel"), name=data["name"], version=data["version"], digest=digest, spec=spec,
        build_status="ready" if artifact else "build_required", runtime_artifact_id=artifact.id if artifact else None, recipe_id=recipe.id,
        eval_set_id=es.id if es else None, profile_id=prep.profile_id, simulated=(resolved.source == "fixture"), weights_qualification=prep.qual,
        notes=data.get("notes", ""), provenance={"resolved_at": iso(utcnow()), "resolver": resolved.verified, "source_note": QWEN_BASELINE["provenance"] if resolved.verified == "supplied" else None, "argv_preview": argv_for(cfg, model_path="<model>", template_path="<template>" if template else None, host="127.0.0.1", port=0, api_key_file="<key>")},
        created_by=created_by,
    )  # fmt: skip
    db.add(row)
    db.flush()
    return row


def create_release(
    db: DbSession, settings: Settings, data: dict[str, Any], created_by: str | None
) -> Release:
    """Prepare and persist in one call. Only for callers whose model source is local (the simulator seed
    with fixture blobs); the API route prepares outside its write transaction and persists inside."""
    return persist_release(db, settings, prepare_release(db, settings, data), created_by)


def release_out(r: Release, brief: bool = False) -> dict[str, Any]:
    spec = r.spec
    out = {
        "id": r.id, "name": r.name, "version": r.version, "digest": r.digest, "build_status": r.build_status, "deployable": r.build_status == "ready" and not r.retired_at,
        "runtime_artifact_id": r.runtime_artifact_id, "recipe_id": r.recipe_id, "eval_set_id": r.eval_set_id, "profile_id": r.profile_id,
        "simulated": r.simulated, "weights_qualification": r.weights_qualification, "notes": r.notes, "created_at": iso(r.created_at), "created_by": r.created_by,
        "retired_at": iso(r.retired_at),
        "model": {k: v for k, v in spec.get("model", {}).items() if k != "gguf"}, "runtime": spec.get("runtime"), "config": spec.get("config"), "platform": spec.get("platform"),
    }  # fmt: skip
    if not brief:
        out["spec"] = spec
        out["provenance"] = r.provenance
    return out


def manifest(r: Release, artifact: RuntimeArtifact | None = None) -> dict[str, Any]:
    """Immutable document the agent consumes (plus artifact transport facts, which are not identity)."""
    return {
        "release_id": r.id,
        "name": r.name,
        "version": r.version,
        "digest": r.digest,
        "build_status": r.build_status,
        "runtime_artifact_id": r.runtime_artifact_id,
        "artifact_size": artifact.archive_size if artifact else None,
        "artifact_storage": artifact.storage if artifact else None,
        "artifact_scope": artifact.scope if artifact else None,
        "eval_set_id": r.eval_set_id,
        "simulated": r.simulated,
        "spec": r.spec,
    }


def plan_for_device(r: Release, d: Device) -> dict[str, Any]:
    from ..ids import aware, utcnow

    t = d.last_telemetry or {}
    live = {k: t.get(k) for k in ("mem_total_mb", "mem_available_mb", "disk_free_mb")}
    # R31 follow-up: freshness is the age of the retained telemetry SAMPLE (stamped when a live report
    # carried telemetry), not of the latest observation, which telemetry-free live reports also refresh
    from ..ids import parse_iso

    sample_at = parse_iso(t["at"]) if t.get("at") else None
    age = (utcnow() - aware(sample_at)).total_seconds() if sample_at else None
    hw = profile(d.profile_id) or profile("jetson-orin-nano-8gb")
    out = plan_budget(r.spec, hw, d.settings or {}, live, observed_age_s=age)
    out["observed_at"] = t.get("at")
    out["device_status"] = __import__("convoy_server.serialize", fromlist=["device_status"]).device_status(d)
    return out


# ---------- plans ----------
DEFAULT_GATES = [
    {"metric": "quality.pass_rate", "op": "min", "limit": 0.8, "required": True},
    {"metric": "errors.count", "op": "max", "limit": 0, "required": True},
    {"metric": "coverage.completed_ratio", "op": "min", "limit": 1.0, "required": True},
    {"metric": "latency.p95_ms", "op": "max", "limit": 15000, "required": True},
]
DEFAULT_WORKLOAD = {
    "warmup": 1,
    "ordering": "fixed",
    "max_tokens": 128,
    "temperature": 0.0,
    "seed": 42,
    "co_workload": "none",
}
DEFAULT_SAMPLE_POLICY = {"probation_min_s": 60, "probation_min_requests": 0, "fresh_eval_max_age_s": 3600}


def create_plan(
    db: DbSession,
    *,
    name: str,
    release_id: str,
    baseline_release_id: str | None,
    eval_set_id: str | None,
    gates: list[dict[str, Any]] | None,
    workload: dict[str, Any] | None,
    sample_policy: dict[str, Any] | None,
    created_by: str | None,
) -> Plan:
    rel = db.get(Release, release_id)
    if not rel:
        raise CatalogError("release not found", 404)
    es_id = eval_set_id or rel.eval_set_id
    es = db.get(EvalSet, es_id) if es_id else None
    if not es:
        raise CatalogError("plan needs an eval set (explicit or from the release)")
    base = db.get(Release, baseline_release_id) if baseline_release_id else None
    if baseline_release_id and not base:
        raise CatalogError("baseline release not found", 404)
    if base and (base.profile_id != rel.profile_id or base.simulated != rel.simulated):
        raise CatalogError("baseline must share the release's profile and simulation flag")
    from ..schemas_catalog import GateIn, SamplePolicyIn, WorkloadIn

    try:
        g = [
            (GateIn(**x) if isinstance(x, dict) else x).model_dump()
            for x in (gates if gates is not None else list(DEFAULT_GATES))
        ]
        for gate in g:
            gate["evidence"] = gate.get("evidence") or METRICS.get(gate["metric"], ("", None))[1]
        wl = WorkloadIn(
            **{
                **DEFAULT_WORKLOAD,
                **(workload.model_dump() if hasattr(workload, "model_dump") else (workload or {})),
            }
        ).model_dump()
        sp = SamplePolicyIn(
            **{
                **DEFAULT_SAMPLE_POLICY,
                **(
                    sample_policy.model_dump()
                    if hasattr(sample_policy, "model_dump")
                    else (sample_policy or {})
                ),
            }
        ).model_dump()
    except Exception as e:  # pydantic ValidationError
        raise CatalogError(f"invalid plan input: {str(e).splitlines()[0][:200]}") from e
    errs = validate_gates(g)
    if errs:
        raise CatalogError("; ".join(errs))
    for gate in g:
        gate.setdefault("required", True)
        gate.setdefault("evidence", METRICS[gate["metric"]][1])
    if not (1 <= int(wl["max_tokens"]) <= int(rel.spec["config"]["n_predict"])):
        raise CatalogError("workload.max_tokens must be within the release n_predict")
    digest = sha(
        {
            "release": rel.digest,
            "baseline": base.digest if base else None,
            "eval_set": es.digest,
            "gates": g,
            "workload": wl,
            "sample_policy": sp,
            "profile": rel.profile_id,
            "evaluator": EVALUATOR_VERSION,
        }
    )
    existing = db.scalar(select(Plan).where(Plan.digest == digest))
    if existing:
        raise CatalogError(f"identical plan exists: {existing.id}", 409)
    row = Plan(
        id=new_id("plan"),
        name=name,
        release_id=rel.id,
        baseline_release_id=base.id if base else None,
        eval_set_id=es.id,
        gates=g,
        workload=wl,
        sample_policy=sp,
        profile_id=rel.profile_id,
        evaluator_version=EVALUATOR_VERSION,
        digest=digest,
        simulated=rel.simulated,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def plan_out(p: Plan) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "release_id": p.release_id,
        "baseline_release_id": p.baseline_release_id,
        "eval_set_id": p.eval_set_id,
        "gates": p.gates,
        "workload": p.workload,
        "sample_policy": p.sample_policy,
        "profile_id": p.profile_id,
        "evaluator_version": p.evaluator_version,
        "digest": p.digest,
        "simulated": p.simulated,
        "created_at": iso(p.created_at),
        "created_by": p.created_by,
    }


def plan_manifest(p: Plan, es: EvalSet) -> dict[str, Any]:
    return {
        **plan_out(p),
        "eval_set": {"id": es.id, "digest": es.digest, "cases": es.cases, "scorer": es.scorer},
    }


__all__ = ["DEFAULT_CONFIG"]
