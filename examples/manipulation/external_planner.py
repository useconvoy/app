"""Prepare persistent trial authority, or run one mission against caller-owned HTTPS.

No remote service, Docker or cloud lifecycle is controlled by this command.
"""

# Import this checkout before potentially stale editable installations.
# ruff: noqa: E402
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import signal
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / path for path in (
    "control-plane/agent", "control-plane/contracts", "control-plane/server", "control-plane/worker",
    "integrations/simulation/src", "integrations/lerobot/src", "integrations/planner/src",
)]
sys.path[:0] = [str(path) for path in SOURCES]

import pipeline
from convoy_agent.coordinator.transport import PlannerHTTP
from convoy_contracts.execution import canonical_digest
from convoy_contracts.grants import GrantVerifier, SigningKeys
from convoy_contracts.pairing import PAIRED_PROFILE, PLANNER_RUNTIME, validate_release_manifest
from convoy_lerobot.acceptance import collect_evidence
from convoy_lerobot.artifact import reference_manifest, verify_assets, verify_versions
from development_signing import development_grants
from planner_evidence import collect_plan, record_outcome

SCOPE = ("One fixed-task real text-planner admission over verified external HTTPS and pretrained "
         "SmolVLA/MuJoCo, seed0 CPU offline lockstep. The endpoint is caller-owned; hosting location "
         "requires separate evidence. No physical robot, general planning or timing-SLA qualification.")


def private_write(path: Path, content: bytes):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def read_file(path: Path, limit: int, *, private=False) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit
                or (private and (info.st_uid != os.geteuid() or info.st_mode & 0o077))):
            raise ValueError("invalid trial input file")
        raw = stream.read(limit + 1)
        if len(raw) != info.st_size:
            raise ValueError("trial input changed while reading")
        return raw


def prepare(directory: Path) -> dict:
    """Initial creation only; partial/existing authority is preserved, never repaired."""
    directory = directory.absolute()
    directory.mkdir(parents=True, mode=0o700, exist_ok=False)
    development_grants(directory)
    private_write(directory / "planner-probe.txt", secrets.token_urlsafe(48).encode("ascii") + b"\n")
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return {"status": "prepared", "authority": "ed25519", "remote_resources_created": False}


def authority(directory: Path) -> tuple[Path, str]:
    directory = directory.absolute()
    for folder in (directory, directory / "api-keys", directory / "verification"):
        info = folder.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError("trial authority must be private and owned by this user")
    signing = directory / "api-keys/execution.json"
    signer = SigningKeys(signing)
    for purpose in ("action", "planner"):
        path = directory / "verification" / (purpose + ".json")
        GrantVerifier(path, purpose=purpose)
        if json.loads(read_file(path, 65536, private=True)) != signer.verification_document(purpose):
            raise ValueError("trial public verification does not match the local signer")
    probe = read_file(directory / "planner-probe.txt", 257, private=True)
    if not re.fullmatch(rb"[A-Za-z0-9_-]{32,256}\n?", probe):
        raise ValueError("invalid planner probe credential file")
    return signing, probe.rstrip(b"\n").decode("ascii")


def strict_json(raw: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate release field")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("nonfinite release value")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def run(args) -> dict:
    # Validate all caller inputs before creating run state or launching children.
    parts = urlsplit(args.planner_url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("external trials require HTTPS")
    signing, probe = authority(args.authority_dir)
    client = PlannerHTTP(args.planner_url, probe, ca_file=args.planner_ca_file)
    manifest = validate_release_manifest(strict_json(read_file(args.manifest, 16384)))
    if (manifest["profile"] != PAIRED_PROFILE or manifest["planner"]["runtime"] != PLANNER_RUNTIME
            or manifest["action_manifest"] != reference_manifest()
            or manifest["placement"]["planner"] not in {"development-local", "development-remote-cpu"}):
        raise ValueError("external trial requires the qualified real paired CPU release")
    if bool(args.direct_report) != bool(args.direct_trace):
        raise ValueError("supply both direct comparison files or neither")
    if args.direct_report:
        if not args.direct_report.is_file() or not args.direct_trace.is_file():
            raise ValueError("direct comparison files unavailable")
    verify_versions()
    assets = args.action_assets.resolve(strict=True)
    verify_assets(assets)
    output = args.output.absolute()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    evidence = {"schema_version": 1, "status": "failed", "scope": SCOPE,
                "release_digest": canonical_digest(manifest), "configured_placement": manifest["placement"],
                "planner_ownership": "external", "planner_backend_kind": "llamacpp-text-model",
                "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    environment = dict(PYTHONPATH=os.pathsep.join(map(str, SOURCES)), CONVOY_SMOLVLA_ASSETS=str(assets),
                       HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
                       TOKENIZERS_PARALLELISM="false", MUJOCO_GL="glfw" if sys.platform == "darwin" else "egl")
    previous = {name: os.environ.get(name) for name in environment}
    try:
        os.environ.update(environment)
        observed = pipeline.probe_external_planner(client, manifest)
        evidence["serving_probe"] = observed
        result = pipeline.run(output / "pipeline", manifest=manifest,
            runtime_factory="convoy_lerobot.runtime:smolvla", coordinator_module="convoy_agent.coordinator.paired",
            policy_kind="pretrained_vision_language_action", external_planner_url=args.planner_url,
            planner_ca_file=args.planner_ca_file, planner_probe_token=probe, execution_signing_keys_file=signing,
            planner_evidence=SCOPE, planner_backend_kind="llamacpp-text-model")
        evidence.update(source_commit=result.get("source_commit"), source_dirty=result.get("source_dirty"))
        collect_evidence(output / "pipeline", args.direct_report, args.direct_trace)
        evidence["plan"] = collect_plan(output / "pipeline", manifest, observed)
        result = json.loads((output / "pipeline/visual-result.json").read_text())
        case = record_outcome(evidence, result, require_comparison=args.direct_report is not None)
        if (case["mission"]["id"] != evidence["plan"]["mission_id"]
                or case["episode"]["summary"]["planner_backend_kind"] != "llamacpp-text-model"
                or case["episode"]["summary"]["planner_accepted"] is not True):
            raise ValueError("episode does not match the accepted real-model plan")
        evidence["actions_sha256"] = hashlib.sha256((output / "pipeline/actions.jsonl").read_bytes()).hexdigest()
        evidence["status"] = "passed"
    except BaseException as error:
        evidence["failure_type"] = type(error).__name__
        raise
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        private_write(output / "external-result.json", json.dumps(evidence, indent=2, allow_nan=False).encode() + b"\n")
    return evidence


class BoundedParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid external planner arguments; use --help.\n")


def main():
    parser = BoundedParser(description=__doc__, allow_abbrev=False)
    commands = parser.add_subparsers(dest="mode", required=True)
    prepare_parser = commands.add_parser("prepare", allow_abbrev=False, help="create fresh persistent local authority")
    prepare_parser.add_argument("--authority-dir", type=Path, required=True)
    run_parser = commands.add_parser("run", allow_abbrev=False, help="run one real mission; never manage the endpoint")
    for name in ("authority-dir", "manifest", "action-assets", "output"):
        run_parser.add_argument("--" + name, type=Path, required=True)
    run_parser.add_argument("--planner-url", required=True)
    run_parser.add_argument("--planner-ca-file", type=Path)
    run_parser.add_argument("--direct-report", type=Path)
    run_parser.add_argument("--direct-trace", type=Path)
    args = parser.parse_args()

    def interrupted(*_):
        raise KeyboardInterrupt("external trial interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = prepare(args.authority_dir) if args.mode == "prepare" else run(args)
    except (Exception, KeyboardInterrupt):
        parser.exit(1, "External planner trial failed; preserve the private authority/run directories for diagnosis.\n")
    finally:
        signal.signal(signal.SIGTERM, previous)
    print(json.dumps({"status": result["status"], "remote_lifecycle": "caller-owned"}))


if __name__ == "__main__":
    main()
