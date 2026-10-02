"""Run native engine checks in a bounded child; reuse enrollment, never print credentials."""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import signal
import time
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def _child(connection, spec, model_spec, asset):
    try:
        if model_spec["engine"] == "mujoco":
            from .mujoco import qualify
        else:
            raise ValueError("Isaac runner is not installed; use a supported Isaac host")
        evidence = qualify(spec, model_spec, Path(asset))
        result = {"state": "passed", "detail": "Model loaded; declared joints/controller and physics steps checked.", "evidence": evidence}
    except Exception as error:
        result = {"state": "failed", "detail": str(error)[:1000], "evidence": {}}
    try:
        connection.send(result)
    finally:
        connection.close()


def check(spec, model_spec, asset, *, timeout_s=60):
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(child, spec, model_spec, str(asset)))
    process.start()
    child.close()
    try:
        if not parent.poll(timeout_s):
            return {"state": "failed", "detail": "Simulator verification exceeded its time budget.", "evidence": {}}
        try:
            return parent.recv()
        except EOFError:
            return {"state": "failed", "detail": "Simulator verification process exited without evidence.", "evidence": {}}
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
        process.join(timeout=2)
        if process.is_alive():
            process.kill()
            process.join(timeout=2)


def reconcile(control, asset_root, *, timeout_s=60):
    desired = control.get("/api/agent/v1/registry")
    request = desired.get("qualification")
    if not request or request["state"] != "requested":
        return {"state": "idle"}
    profile = desired["profile"]
    model_spec = next(m for m in profile["spec"]["simulations"] if m["engine"] == request["engine"])
    digest = model_spec["asset"]["sha256"]
    # Files are provisioned by the owner under their content digest. No remote URL fetch, shell
    # interpolation or arbitrary path supplied by a planner/server is performed by the runner.
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid pinned asset digest")
    result = check(profile["spec"], model_spec, Path(asset_root) / digest, timeout_s=timeout_s)
    report = {**result, "profile_digest": request["profile_digest"],
              "binding_epoch": request["binding_epoch"], "asset_sha256": digest if result["state"] == "passed" else None}
    response = control.post(f"/api/agent/v1/qualifications/{request['id']}/report", report)
    return {"id": response["id"], "state": response["state"], "detail": result["detail"]}


def main(argv=None):
    from convoy_agent.agent import AgentConfig
    from convoy_agent.coordinator.transport import JsonHTTP

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True, help="existing simulator enrollment directory")
    parser.add_argument("--assets", type=Path, required=True, help="directory of robot bundles named by SHA-256")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    cfg = AgentConfig(args.data_dir)
    if not cfg.credential or not cfg.data.get("simulate"):
        parser.error("enroll a simulator device first; this runner never commands a physical robot")
    control = JsonHTTP(cfg.data["server"], cfg.credential, ca_file=cfg.data.get("ca_file"))
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        try:
            result = reconcile(control, args.assets)
            if args.once or result["state"] != "idle":
                print(json.dumps(result), flush=True)
            if args.once:
                return int(result["state"] == "failed")
        except Exception as error:
            # Transport exceptions contain classifications, never credentials or headers.
            LOGGER.warning("Qualification request failed: %s", type(error).__name__)
            if args.once:
                return 2
        for _ in range(20):
            if stopping:
                break
            time.sleep(0.25)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
