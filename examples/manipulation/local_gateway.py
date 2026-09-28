"""Own a real, pinned CPU text runtime for local development qualification.

Run in the optional lerobot `paired` environment. This does not install an agent,
enroll hardware, contact a cloud provider, or replace an existing model owner.
The output directory must be new. The native child and loopback gateway stop
together on exit; ambiguous runtime failure is never automatically restarted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import signal
import tarfile
import threading
import time
from pathlib import Path

from convoy_agent.gateway import Gateway
from convoy_agent.runtime import RuntimeSupervisor
from convoy_planner.artifact import validate_gateway_identity


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def prepare(receipt: dict, output: Path) -> tuple[Path, Path]:
    if receipt["schema_version"] != 1:
        raise ValueError("unsupported local asset receipt")
    model = Path(receipt["model"]["path"]).resolve()
    if model.stat().st_size != receipt["model"]["bytes"] or sha256(model) != receipt["model"]["sha256"]:
        raise ValueError("model bytes do not match the supplied pin")
    archive = Path(receipt["archive"]["path"]).resolve()
    if sha256(archive) != receipt["archive"]["sha256"]:
        raise ValueError("runtime archive differs from the supplied pin")
    extracted = output / "runtime"
    extracted.mkdir(mode=0o700)
    with tarfile.open(archive) as source:
        source.extractall(extracted, filter="data")
    original_root = Path(receipt["runtime_root"]).resolve()
    binary = None
    for record in [receipt["binary"], *receipt["libraries"]]:
        relative = Path(record["path"]).resolve().relative_to(original_root)
        member = (extracted / relative).resolve()
        member.relative_to(extracted.resolve())
        if sha256(member) != record["sha256"]:
            raise ValueError("extracted runtime member differs from the supplied pin")
        if record is receipt["binary"]:
            binary = member
    return model, binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", type=Path, required=True, help="verified local model/runtime receipt")
    parser.add_argument("--output", type=Path, required=True, help="new private gateway state directory")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    receipt = json.loads(args.assets.read_text())
    model, binary = prepare(receipt, output)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    supervisor = RuntimeSupervisor(output / "native", simulate=False)
    # Bound-completion calls acquire the inference slot without waiting. The
    # legacy gateway's admission counter must still allow the first caller.
    gateway = Gateway(supervisor, host="127.0.0.1", queue_depth=1, deadline_s=30)
    config = {"ctx_size": 2048, "n_predict": 128, "gpu_layers": 0}
    evidence = {
        "scope": "local CPU text inference; no Jetson, cloud or physical-robot qualification",
        "host": {"system": platform.system(), "machine": platform.machine()},
        "launcher_sha256": sha256(Path(__file__)),
        "model": {"sha256": receipt["model"]["sha256"], "bytes": receipt["model"]["bytes"]},
        "runtime_source": receipt["source"], "runtime_archive_sha256": receipt["archive"]["sha256"],
        "status": "starting",
    }
    try:
        native = supervisor.start(
            release_id="local-cpu-" + receipt["archive"]["sha256"][:16],
            spec={"model": {"file": {"sha256": receipt["model"]["sha256"]}},
                  "runtime": {"artifact_sha256": receipt["archive"]["sha256"]}, "config": config},
            model_path=model, template_path=None, binary=binary,
            lib_dir=output / "runtime/lib", health_timeout_s=120,
        )
        if native["simulated"] or native["backend"] != "CPU":
            raise ValueError("native runtime did not establish the requested CPU backend")
        gateway.start()
        gateway.set_mode("production")
        identity = validate_gateway_identity(gateway.runtime_identity(check_health=True))
        evidence.update(status="ready", gateway_url=f"http://127.0.0.1:{gateway.port}",
                        gateway_identity=identity, effective_configuration=supervisor.config,
                        native={key: native[key] for key in (
                            "build_info", "binary_sha256", "chat_template_sha256", "backend",
                            "gpu_offloaded_layers", "gpu_total_layers", "n_ctx", "total_slots",
                        )})
        (output / "gateway.json").write_text(json.dumps(evidence, indent=2) + "\n")
        print(f"Real local CPU gateway ready: {output / 'gateway.json'}", flush=True)
        while not stopped.wait(0.2):
            if gateway.needs_restart or supervisor.state() != "running":
                raise RuntimeError("native execution requires explicit recovery; no automatic restart")
    except BaseException as error:
        evidence.update(status="failed", error_type=type(error).__name__)
        raise
    finally:
        gateway.set_mode("closed")
        gateway.stop()
        cleanup = supervisor.stop(deadline=time.monotonic() + 10)
        evidence.update(cleanup=cleanup, gateway_drained=gateway.drain(2), gateway_stats=gateway.stats)
        if evidence["status"] == "ready":
            evidence["status"] = "stopped"
        (output / "gateway-result.json").write_text(json.dumps(evidence, indent=2) + "\n")
        if not cleanup["stopped"] or not evidence["gateway_drained"]:
            raise RuntimeError("owned gateway cleanup incomplete; inspect retained local state")


if __name__ == "__main__":
    main()
