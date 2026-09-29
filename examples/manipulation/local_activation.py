"""Qualify local A -> B -> A activation with real Qwen/SmolVLA and no downloads.

--serve completes all three explicit missions first, then retains the ready, idle
installation for console use. The output directory is private and must be new.
"""

# Import this checkout after source-path setup, rather than stale editable installs.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse

# Run this checkout without changing the optional environment's editable installs.
ROOT = Path(__file__).resolve().parents[2]
SOURCES = [ROOT / path for path in (
    "control-plane/agent", "control-plane/contracts", "control-plane/server", "control-plane/worker",
    "integrations/simulation/src", "integrations/lerobot/src", "integrations/planner/src",
)]
sys.path[:0] = [str(path) for path in SOURCES]

import httpx
from convoy_agent.agent import enroll
from convoy_agent.owned_process import OwnedProcess, _psutil
from convoy_agent.runtime_args import canonical_config
from convoy_contracts.execution import canonical_digest
from convoy_contracts.pairing import (
    CATALOG_SHA256,
    FIXED_TASK,
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    SKILL_ID,
    validate_plan_result,
    validate_release_manifest,
)
from convoy_lerobot.artifact import reference_manifest
from convoy_lerobot.local_recipe import load_registry
from convoy_planner.artifact import (
    STATIC_GATEWAY_FIELDS,
    artifact_descriptor,
    validate_gateway_identity,
)
from convoy_planner.local_assets import validate_text_receipt
from development_signing import development_grants


def require(condition, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def write_json(path: Path, value) -> None:
    """Private, atomic evidence and connection files; never print credentials."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def read_json(path: Path) -> dict:
    require(path.stat().st_size <= 4 * 1024 * 1024, "JSON input exceeds its byte bound")
    return json.loads(path.read_text())


def recipes(text_assets: Path, action_assets: Path, qualification: Path) -> tuple[dict, dict]:
    receipt = validate_text_receipt(read_json(text_assets))
    qualified = read_json(qualification)
    require(qualified.get("status") == "passed", "native qualification did not pass")
    entries = qualified.get("inferences", [])
    require(len(entries) == 2 and {item["context"] for item in entries} == {2048, 4096},
            "native qualification must contain exactly contexts 2048 and 4096")
    result, history = [], {}
    for entry in sorted(entries, key=lambda item: item["context"]):
        identity = validate_gateway_identity(entry["gateway_identity"])
        require(entry["decision"] == {"kind": "skill", "skill_id": SKILL_ID, "parameters": {}},
                "native qualification did not admit the fixed skill")
        config = {"ctx_size": entry["context"], "n_predict": 128, "gpu_layers": 0}
        require(canonical_digest(canonical_config(config)) == identity["config_sha256"],
                "native qualification context does not match its immutable identity")
        for field, kind in (("model_sha256", "model"), ("runtime_artifact_sha256", "archive"),
                            ("binary_sha256", "binary")):
            require(identity[field] == receipt[kind]["sha256"], "text receipt differs from native qualification")
        manifest = validate_release_manifest({
            "schema_version": 2, "profile": PAIRED_PROFILE, "action_manifest": reference_manifest(),
            "planner": {"runtime": PLANNER_RUNTIME, "artifact_sha256": canonical_digest(artifact_descriptor(identity)),
                        "protocol_sha256": PLANNER_PROTOCOL_SHA256},
            "task": {"instruction": FIXED_TASK, "skill_id": SKILL_ID}, "catalog_sha256": CATALOG_SHA256,
            "planning": {"timeout_ms": 30000},
            "placement": {"policy": "development-local-cpu", "planner": "development-local"},
        })
        result.append({"manifest": manifest, "gateway_identity": identity, "text_assets": receipt,
                       "action_assets": str(action_assets.resolve(strict=True)), "native_config": config})
        history[str(entry["context"])] = entry["planner_artifact_sha256"]
    require(canonical_digest(result[0]["manifest"]) != canonical_digest(result[1]["manifest"]),
            "A and B must identify different immutable bundles")
    return {"schema_version": 1, "recipes": result}, {
        "sha256": hashlib.sha256(qualification.read_bytes()).hexdigest(),
        "source_commit": qualified.get("source_commit"), "historical_planner_artifacts": history,
        "note": "Saved native identities are unchanged; these missions qualify the current paired descriptors.",
    }


def child_environment() -> dict[str, str]:
    names = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "SYSTEMROOT", "VIRTUAL_ENV")
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update(PYTHONPATH=os.pathsep.join(map(str, SOURCES)), PYTHONUNBUFFERED="1",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1",
               TOKENIZERS_PARALLELISM="false")
    return env


def journal_read(output: Path, query: str, parameters=()):
    path = output / "robot/coordinator/execution.sqlite3"
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5) as journal:
        return journal.execute(query, parameters).fetchall()


def process_gone(record: dict) -> bool:
    """Read-only identity comparison; never signal a numeric PID from evidence."""
    psutil = _psutil()
    if record.get("pid") is None:
        return record.get("state") == "stopped"
    try:
        process = psutil.Process(record["pid"])
        return (OwnedProcess._created(process) != record["created"]
                or process.status() == psutil.STATUS_ZOMBIE or not process.is_running())
    except psutil.NoSuchProcess:
        return True


def loopback_port(url: str) -> int:
    parsed = urlparse(url)
    require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port is not None
            and parsed.username is None and parsed.password is None and parsed.path in ("", "/")
            and not parsed.query and not parsed.fragment, "unexpected local component endpoint")
    return parsed.port


def listener_closed(url: str) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.25)
        return connection.connect_ex(("127.0.0.1", loopback_port(url))) != 0


def snapshot(output: Path, deployment: dict, recipe: dict) -> tuple[dict, dict]:
    directory = output / "robot/local-bundle"
    state = read_json(directory / "bundle.json")
    require(state["phase"] == "verified" and state["target"] == {
        "deployment_id": deployment["id"], "generation": deployment["generation"],
        "release_digest": canonical_digest(recipe["manifest"]),
    }, "local owner did not verify this deployment")
    bundle = state["previous_verified_bundle"]
    observed = json.loads(journal_read(output, "SELECT value FROM meta WHERE key='observed_bundle'")[0][0])
    require(observed["binding_id"] == bundle["binding_id"]
            and observed["deployment_id"] == deployment["id"]
            and observed["generation"] == deployment["generation"]
            and observed["observation"] == bundle["observation"], "durable binding differs from ready deployment")
    actual = validate_gateway_identity(bundle["gateway_identity"])
    require(all(actual[key] == recipe["gateway_identity"][key] for key in STATIC_GATEWAY_FIELDS),
            "loaded native identity differs from the unchanged qualification pins")
    require(set(bundle["endpoints"]) == {"native", "gateway", "worker", "planner"},
            "owner must expose all four local endpoints for cleanup verification")
    for url in bundle["endpoints"].values():
        loopback_port(url)
        require(not listener_closed(url), "a verified component listener disappeared")
    records = {}
    for role in ("native", "worker", "planner"):
        record = read_json(directory / role / "process.json")
        require(record["state"] == "running", "verified component lacks a running ownership record")
        process = _psutil().Process(record["pid"])
        executable = process._proc.exe()  # Same pinned native API used by OwnedProcess; no argv fallback.
        require(OwnedProcess._created(process) == record["created"]
                and tuple(process.uids()) == (os.getuid(),) * 3 and record["uid"] == os.getuid()
                and executable and os.path.realpath(executable) == record["exe"]
                and process.cmdline().count(record["marker"]) == 1 and process.is_running(),
                "component process identity is unverified")
        records[role] = record
    return {"binding": bundle, "observed": observed,
            "processes": {role: {key: record[key] for key in ("pid", "created")} for role, record in records.items()}}, records


def mission_evidence(output: Path, mission: dict, episode: dict, manifest: dict) -> dict:
    mission_id = mission["id"]
    rows = journal_read(output, "SELECT request_json,result_json,state FROM plans WHERE mission_id=?", (mission_id,))
    require(len(rows) == 1 and rows[0][2] == "accepted", "mission requires one durable accepted real planner proposal")
    request, plan = json.loads(rows[0][0]), validate_plan_result(json.loads(rows[0][1]))
    require(request["identity"]["release_digest"] == canonical_digest(manifest)
            and plan["planner_artifact_sha256"] == manifest["planner"]["artifact_sha256"],
            "planner evidence differs from the requested bundle")
    trace = []
    for sequence, raw_request, raw_result, raw_outcome, state in journal_read(output,
            "SELECT sequence,request_json,result_json,observation_json,state FROM commands "
            "WHERE mission_id=? ORDER BY sequence", (mission_id,)):
        action_request, result, outcome = json.loads(raw_request), json.loads(raw_result), json.loads(raw_outcome)
        require(state == "applied" and sequence == len(trace), "action trace is not consecutive and applied")
        trace.append({"mission_id": mission_id, "sequence": sequence, "action": result["action"],
                      "observation_sha256": canonical_digest(action_request["observation"]),
                      "policy_duration_ms": result["policy_duration_ms"], "reward": outcome["reward"],
                      "success": outcome["success"], "command_state": state})
    summary = episode["summary"]
    require(mission["state"] == "completed" and summary["final_success"] is True
            and summary["planner_accepted"] is True and summary["planner_backend_kind"] == "llamacpp-text-model"
            and summary["policy_runtime"] == manifest["action_manifest"]["policy"]["runtime"]
            and len(trace) == summary["steps"] and len(trace) > 0 and trace[-1]["success"] is True,
            "mission did not demonstrate real planner admission and successful learned actions")
    write_json(output / f"actions-{mission_id}.json", trace)
    return {"request": request, "result": plan, "state": "accepted", "applied_actions": len(trace)}


def stop_child(child: subprocess.Popen | None, timeout: float) -> bool:
    if child is None or child.poll() is not None:
        return False
    child.terminate()
    try:
        child.wait(timeout=timeout)
        return False
    except subprocess.TimeoutExpired:
        child.kill()  # Retained direct-child Popen, never a reconstructed numeric PID.
        child.wait(timeout=5)
        return True


def cleanup_bundle(output: Path, captured: list[dict], endpoints: set[str]) -> dict:
    directory = output / "robot/local-bundle"
    state_path = directory / "bundle.json"
    errors, recovered = [], []
    native_markers = {records["native"]["marker"] for records in captured}
    state = None
    try:
        state = read_json(state_path) if state_path.exists() else None
    except (OSError, ValueError, RuntimeError) as error:
        errors.append(f"bundle record: {type(error).__name__}")
    # The coordinator has exited before this runs. Recovery acquires each exact
    # installation lock and uses the helper's full verification, including intents.
    for role in ("planner", "worker", "native"):
        path = directory / role / "process.json"
        if not path.exists():
            continue
        try:
            with OwnedProcess(path.parent) as owner:
                before = read_json(path)
                if role == "native":
                    native_markers.add(before["marker"])
                if before.get("state") != "stopped":
                    owner.recover(terminate_timeout=3, kill_timeout=3)
                    recovered.append(role)
                record = read_json(path)
                require(record["state"] == "stopped" and process_gone(record), "owned process cleanup unresolved")
        except Exception as error:  # noqa: BLE001 — retain failure and attempt the other owned roles.
            errors.append(f"{role}: {type(error).__name__}")
    if state and state.get("previous_verified_bundle"):
        endpoints.update(state["previous_verified_bundle"].get("endpoints", {}).values())
    for records in captured:
        for role, record in records.items():
            try:
                require(process_gone(record), "previous process incarnation is still live")
            except Exception as error:  # noqa: BLE001 — all captured incarnations must be checked.
                errors.append(f"captured {role}: {type(error).__name__}")
    for url in sorted(endpoints):
        try:
            require(listener_closed(url), "owned listener is still open")
        except Exception as error:  # noqa: BLE001 — retain every unresolved listener.
            errors.append(f"listener {url}: {type(error).__name__}")
    graceful = state is None or (state.get("phase") == "stopped" and state.get("cleanup") == {
        "gateway": True, "planner": True, "worker": True, "native": True,
    })
    # The native marker contains its API credential. Process exit is insufficient:
    # RuntimeSupervisor must also have retired each delivered credential file.
    native_keys_removed = (all(not Path(marker).exists() for marker in native_markers)
                           and not any((directory / "native").glob("launch-*")))
    return {"verified": not errors, "graceful_owner_record": graceful, "recovered_roles": recovered,
            "native_credentials_removed": native_keys_removed,
            "owner_phase": state.get("phase") if state else "not_started", "errors": errors,
            "listeners_checked": sorted(endpoints), "captured_bindings_checked": len(captured)}


def run(args) -> dict:
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    os.chmod(output, 0o700)
    spec = importlib.util.spec_from_file_location("convoy_activation_pipeline", Path(__file__).with_name("pipeline.py"))
    pipeline = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pipeline)
    api_child = coordinator = None
    base = None
    logs, captured, endpoints = [], [], set()
    evidence = {"status": "running", "cases": [], "database": "one retained local SQLite database",
                "scope": "Actual local CPU Qwen planning and pretrained SmolVLA/MuJoCo actions; seed0, offline lockstep. "
                         "A/B use the same weights with contexts 2048/4096. No hosted, Jetson, physical or real-time qualification."}

    def start(label, env, *arguments):
        log = (output / f"{label}.log").open("wb")
        logs.append(log)
        return subprocess.Popen([sys.executable, "-m", *map(str, arguments)], env=env, cwd=ROOT,
                                stdout=log, stderr=subprocess.STDOUT)

    try:
        evidence["source_commit"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        evidence["source_dirty"] = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
        registry, evidence["native_qualification"] = recipes(args.text_assets, args.action_assets, args.gateway_qualification)
        registry_path = output / "local-recipes.json"
        write_json(registry_path, registry)
        load_registry(registry_path)  # Same bounded private registry parser used by the real owner.
        base = f"http://127.0.0.1:{pipeline.free_port()}"
        email, password = "developer@convoy.local", secrets.token_urlsafe(32)
        api_signing, verification = development_grants(output)
        scoped = {name: secrets.token_urlsafe(48) for name in (
            "CONVOY_WORKER_PROBE_TOKEN", "CONVOY_PLANNER_PROBE_TOKEN",
        )}
        api_env = {**child_environment(), "CONVOY_DATA_DIR": str(output / "server"), "CONVOY_SIMULATOR": "1",
                   "CONVOY_SCHEDULER_INPROCESS": "0", "CONVOY_ADMIN_EMAIL": email, "CONVOY_ADMIN_PASSWORD": password,
                   "CONVOY_PUBLIC_URL": base, "CONVOY_LOG_LEVEL": "WARNING",
                   "CONVOY_REPLAY_JOURNAL": str(output / "robot/coordinator/execution.sqlite3"),
                   **api_signing}
        coordinator_env = {**child_environment(), **scoped, **verification}
        evidence["authorization"] = {"mode": "Ed25519 JWT", "issuer": "convoy-development-api",
            "private_signing_config": "API process only", "model_config": "purpose-scoped public verification files",
            "same_user_development": True}
        api_child = start("api", api_env, "convoy_server.cli", "serve", "--host", "127.0.0.1", "--port", loopback_port(base))
        with httpx.Client(base_url=base, timeout=5, trust_env=False) as api:
            pipeline.wait_for(lambda: api.get("/api/health"), lambda response: response.status_code == 200)
            response = api.post("/api/v1/auth/login", json={"email": email, "password": password})
            response.raise_for_status()
            api.headers["X-Convoy-Client"] = "web"

            def post(path, body, key=None):
                response = api.post(path, json=body, headers={"Idempotency-Key": key or str(uuid.uuid4())})
                response.raise_for_status()
                return response.json()

            def get(path):
                require(api_child.poll() is None and (coordinator is None or coordinator.poll() is None),
                        "a development service exited; inspect the retained logs")
                response = api.get(path)
                response.raise_for_status()
                return response.json()

            project = post("/api/v1/projects", {"name": "Local model activation"})
            bootstrap = post("/api/v1/enrollments", {"label": "local paired simulator", "simulated": True})
            enrolled = enroll(output / "robot", server=base, token=bootstrap["token"], name="Virtual Sawyer", simulate=True)
            robot = post("/api/v1/robots", {"project_id": project["id"], "device_id": enrolled["device_id"],
                                           "name": "Virtual Sawyer", "profile": PAIRED_PROFILE})
            application = post("/api/v1/applications", {"project_id": project["id"], "name": "Qwen and SmolVLA pick and place"})
            releases = [post(f"/api/v1/applications/{application['id']}/releases", {"manifest": recipe["manifest"]})
                        for recipe in registry["recipes"]]
            evidence.update(robot_id=robot["id"], application_id=application["id"],
                            releases={name: release for name, release in zip(("A", "B"), releases, strict=True)})
            coordinator = start("coordinator", coordinator_env, "convoy_agent.coordinator.paired",
                                "--data-dir", output / "robot", "--robot-id", robot["id"], "--local-registry", registry_path)
            evidence["installation_processes"] = {"api_pid": api_child.pid, "coordinator_pid": coordinator.pid}
            generation = 0
            for label, index in (("A", 0), ("B", 1), ("A-restored", 0)):
                recipe, release = registry["recipes"][index], releases[index]
                deployment = post("/api/v1/deployments", {"robot_id": robot["id"], "release_id": release["id"],
                                                         "expected_generation": generation})
                evidence["pending_deployment"] = {"case": label, "deployment": deployment}
                write_json(output / "local-activation-result.json", evidence)
                ready = pipeline.wait_for(lambda deployment=deployment: get(f"/api/v1/deployments/{deployment['id']}"),
                                          lambda value: value["state"] in {"ready", "blocked"}, timeout=180)
                evidence["pending_deployment"]["deployment"] = ready
                require(ready["state"] == "ready", "deployment was blocked; inspect its failure and retained owner records")
                generation = deployment["generation"]
                binding, records = snapshot(output, deployment, recipe)
                if captured:
                    previous = evidence["cases"][-1]["activation"]
                    require(previous["binding"]["binding_id"] != binding["binding"]["binding_id"], "replacement reused old binding")
                    require(all(process_gone(record) for record in captured[-1].values()), "replacement leaked a previous process")
                    require(all(records[role]["created"] != old["created"] or records[role]["pid"] != old["pid"]
                                for role, old in captured[-1].items()), "replacement reused a process incarnation")
                    new_urls = set(binding["binding"]["endpoints"].values())
                    require(all(listener_closed(url) for url in previous["binding"]["endpoints"].values() if url not in new_urls),
                            "replacement left an old listener open")
                captured.append(records)
                endpoints.update(binding["binding"]["endpoints"].values())
                missions = get(f"/api/v1/missions?project_id={project['id']}")
                require(len(missions) == len(evidence["cases"]), "deployment automatically started an unexpected mission")
                case = {"case": label, "deployment": ready, "activation": binding, "no_automatic_mission": True}
                evidence["cases"].append(case)
                evidence.pop("pending_deployment")
                write_json(output / "local-activation-result.json", evidence)
                ttl = min(300, recipe["manifest"]["action_manifest"]["execution"]["mission_timeout_s"])
                body = {"deployment_id": deployment["id"], "expected_generation": generation, "seed": 0, "ttl_s": ttl}
                key = str(uuid.uuid4())
                mission = post(f"/api/v1/robots/{robot['id']}/missions", body, key)
                require(post(f"/api/v1/robots/{robot['id']}/missions", body, key)["id"] == mission["id"],
                        "start retry created a second mission")
                case["mission"] = pipeline.wait_for(lambda mission=mission: get(f"/api/v1/missions/{mission['id']}"),
                    lambda item: item["state"] in {"completed", "failed", "cancelled", "unknown"}, timeout=ttl + 15)
                case["episode"] = get(f"/api/v1/episodes/{case['mission']['episode_id']}")
                case["plan"] = mission_evidence(output, case["mission"], case["episode"], recipe["manifest"])
                write_json(output / "local-activation-result.json", evidence)
                print(f"{label}: generation {generation}, accepted Qwen plan, {case['plan']['applied_actions']} learned actions, success.", flush=True)
            require(journal_read(output, "SELECT count(*) FROM plans WHERE state='accepted'")[0][0] == 3,
                    "acceptance requires exactly three durable accepted plans")
            evidence["status"] = "passed"
            write_json(output / "local-activation-result.json", evidence)
            if args.serve:
                write_json(output / "connection.json", {"api_url": base, "email": email, "password": password,
                    "project_id": project["id"], "robot_id": robot["id"], "application_id": application["id"],
                    "release_id": releases[0]["id"], "release_ids": {"A": releases[0]["id"], "B": releases[1]["id"]},
                    "harness_pid": os.getpid(), "planner_backend_kind": "llamacpp-text-model",
                    "mode": "A-to-B-to-A-completed; ready and idle"})
                print("Three missions passed. Ready and idle; private settings written to connection.json.", flush=True)
                try:
                    while True:
                        threading.Event().wait(0.5)
                        get("/api/health")
                except KeyboardInterrupt:
                    evidence["serve_stopped"] = True
    except BaseException as error:
        evidence.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        cleanup = {}
        # Repeated terminal signals must not interrupt verified child cleanup.
        saved = {sig: signal.signal(sig, signal.SIG_IGN) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            cleanup["coordinator_forced"] = stop_child(coordinator, 30)
            cleanup["bundle"] = cleanup_bundle(output, captured, endpoints)
        except BaseException as error:  # noqa: BLE001 — API cleanup must still run after owner cleanup fails.
            cleanup["error_type"] = type(error).__name__
        finally:
            try:
                cleanup["api_forced"] = stop_child(api_child, 10)
                cleanup["api_listener_closed"] = base is None or listener_closed(base)
            except BaseException as error:  # noqa: BLE001 — persist the failed cleanup receipt.
                cleanup["api_error_type"] = type(error).__name__
            for log in logs:
                log.close()
            for sig, handler in saved.items():
                signal.signal(sig, handler)
        evidence["cleanup"] = cleanup
        if (cleanup.get("error_type") or cleanup.get("api_error_type") or cleanup.get("coordinator_forced")
                or cleanup.get("api_forced") or not cleanup.get("bundle", {}).get("verified")
                or not cleanup.get("api_listener_closed")
                or not cleanup.get("bundle", {}).get("graceful_owner_record")
                or not cleanup.get("bundle", {}).get("native_credentials_removed")):
            evidence["status"] = "failed"
        write_json(output / "local-activation-result.json", evidence)
        if evidence["status"] == "failed" and sys.exc_info()[0] is None:
            raise RuntimeError("activation cleanup failed; inspect local-activation-result.json")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-assets", type=Path, required=True, help="existing pinned native text asset receipt")
    parser.add_argument("--action-assets", type=Path, required=True, help="existing verified SmolVLA asset directory")
    parser.add_argument("--gateway-qualification", type=Path,
                        default=Path(__file__).with_name("evidence") / "native-qwen-local-activation.json")
    parser.add_argument("--output", type=Path, required=True, help="new private run directory; never reused")
    parser.add_argument("--serve", action="store_true", help="complete A/B/A then retain the ready, idle installation")
    args = parser.parse_args()

    def interrupted(*_):
        raise KeyboardInterrupt("local activation interrupted")

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        result = run(args)
        print(json.dumps({"status": result["status"], "missions": len(result["cases"]),
                          "cleanup_verified": result["cleanup"]["bundle"]["verified"]}))
    finally:
        signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    main()
