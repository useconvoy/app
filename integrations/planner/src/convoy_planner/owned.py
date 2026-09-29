"""One Linux ARM64 CPU planner lifetime, with public verification authority only.

The native model and gateway stay on loopback. Exposing the planner listener
requires separately qualified TLS ingress. This launcher does not enroll robots,
execute actions, restart a failed native model, or replay a mission.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import signal
import socket
import threading
import time
from pathlib import Path

from convoy_agent.gateway import Gateway
from convoy_agent.owned_process import OwnedProcess
from convoy_agent.runtime import RuntimeSupervisor
from convoy_agent.runtime_args import canonical_config
from convoy_contracts.execution import canonical_digest
from convoy_contracts.grants import GrantVerifier
from convoy_contracts.pairing import (
    PAIRED_PROFILE,
    PLANNER_PROTOCOL_SHA256,
    PLANNER_RUNTIME,
    validate_release_manifest,
)
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .app import create_app
from .artifact import artifact_descriptor, validate_gateway_identity
from .backend import GatewayBackend
from .local_assets import prepare_text_assets, sha256, validate_text_receipt

FORBIDDEN_ENV = (
    "CONVOY_EXECUTION_SIGNING_KEYS_FILE", "CONVOY_EXECUTION_SIGNING_JSON",
    "CONVOY_ACTION_VERIFICATION_KEYS_FILE", "CONVOY_ACTION_VERIFICATION_JSON",
    "CONVOY_PLANNER_VERIFICATION_JSON", "CONVOY_EXECUTION_SECRET",
    "CONVOY_PLANNER_EXECUTION_SECRET", "CONVOY_WORKER_PROBE_TOKEN",
    "CONVOY_PLANNER_RELEASE_JSON", "CONVOY_PLANNER_RELEASE_SHA256",
)


class StartupCancelled(BaseException):
    """Unwind synchronous native startup before entering ordinary cleanup."""


class StartupTimeout(BaseException):
    """The whole startup budget elapsed, including asset hashing and warmup."""


def read_json(path: Path) -> dict:
    with path.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("launcher input exceeds its bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("launcher input must be an object")
    return value


def preflight(args) -> tuple[dict, dict | None, dict]:
    # Presence, including empty values, must fail closed before native launch.
    if any(name in os.environ for name in FORBIDDEN_ENV):
        raise ValueError("owned planner accepts only its public verification file and probe credential")
    authorization = {}
    manifest = None
    if args.mode == "serve":
        if args.manifest is None:
            raise ValueError("serve requires an immutable paired manifest")
        path = os.environ.get("CONVOY_PLANNER_VERIFICATION_KEYS_FILE")
        probe = os.environ.get("CONVOY_PLANNER_PROBE_TOKEN", "")
        if not path or len(probe) < 32:
            raise ValueError("serve requires planner public verification keys and a probe credential")
        authorization = {"grant_verifier": GrantVerifier(Path(path), purpose="planner"), "probe_token": probe}
        manifest = validate_release_manifest(read_json(args.manifest))
        if manifest["profile"] != PAIRED_PROFILE or manifest["planner"]["runtime"] != PLANNER_RUNTIME:
            raise ValueError("owned planner requires the real paired planner runtime")
        if manifest["placement"]["planner"] not in {"development-local", "development-remote-cpu"}:
            raise ValueError("owned CPU planner requires a local or remote CPU placement declaration")
    receipt = validate_text_receipt(read_json(args.assets))
    expected = {"system": "Linux", "machine": "aarch64"}
    if ({"system": platform.system(), "machine": platform.machine()} != expected
            or {key: receipt["host"][key] for key in expected} != expected):
        raise ValueError("owned CPU planner requires Linux aarch64 assets on Linux aarch64")
    return receipt, manifest, authorization


class OwnedPlanner:
    def __init__(self, receipt: dict, output: Path, owner: OwnedProcess, *, ctx_size: int, stop_timeout_s: float,
                 threads: int = 2, threads_batch: int = 2):
        self.receipt, self.output = receipt, output
        self.ctx_size, self.stop_timeout_s = ctx_size, stop_timeout_s
        self.native_config = canonical_config({"ctx_size": ctx_size, "n_predict": 128, "gpu_layers": 0,
                                               "threads": threads, "threads_batch": threads_batch})
        self.supervisor = RuntimeSupervisor(output / "native", simulate=False, process_owner=owner)
        self.gateway = Gateway(self.supervisor, host="127.0.0.1", queue_depth=1, deadline_s=30)
        self.stop_requested = threading.Event()
        self.accepting = False
        self.starting = True
        self.failure = None
        self.identity = self.backend = None
        self.server = self.server_thread = self.listener = None
        self.evidence = {
            "schema_version": 1, "status": "starting",
            "scope": "Linux ARM64 CPU text planner; no cloud, Jetson, policy or robot qualification",
            "host": {"system": platform.system(), "machine": platform.machine()},
            "launcher_sha256": sha256(Path(__file__)), "runtime_source": receipt["source"],
            "runtime_archive_sha256": receipt["archive"]["sha256"],
            "model": {"sha256": receipt["model"]["sha256"], "bytes": receipt["model"]["bytes"]},
        }

    def signal_stop(self, *_):
        self.accepting = False
        self.stop_requested.set()
        if self.starting:
            raise StartupCancelled()

    def fail(self, reason: str):
        self.failure = self.failure or reason
        self.accepting = False
        self.stop_requested.set()

    def start(self, startup_timeout_s: float):
        model, binary = prepare_text_assets(self.receipt, self.output)
        native = self.supervisor.start(
            release_id="linux-cpu-" + self.receipt["archive"]["sha256"][:16],
            spec={"model": {"file": {"sha256": self.receipt["model"]["sha256"]}},
                  "runtime": {"artifact_sha256": self.receipt["archive"]["sha256"]},
                  "config": self.native_config},
            model_path=model, template_path=None, binary=binary,
            lib_dir=self.output / "runtime/lib", health_timeout_s=startup_timeout_s,
        )
        self.evidence.update(effective_configuration=self.supervisor.config,
                             native={key: native.get(key) for key in (
                                 "build_info", "binary_sha256", "chat_template_sha256", "backend",
                                 "gpu_offloaded_layers", "gpu_total_layers", "n_ctx", "total_slots",
                             )})
        if (native.get("simulated") is not False or native.get("backend") != "CPU"
                or native.get("binary_sha256") != self.receipt["binary"]["sha256"]):
            raise ValueError("loaded runtime did not establish the pinned CPU identity")
        self.gateway.start()
        self.gateway.set_mode("production")
        self.backend = GatewayBackend(f"http://127.0.0.1:{self.gateway.port}")
        self.identity = validate_gateway_identity(self.backend.inspect())
        if (self.identity["model_sha256"] != self.receipt["model"]["sha256"]
                or self.identity["runtime_artifact_sha256"] != self.receipt["archive"]["sha256"]
                or self.identity["binary_sha256"] != self.receipt["binary"]["sha256"]
                or self.identity["config_sha256"] != canonical_digest(self.supervisor.config)):
            raise ValueError("gateway identity differs from loaded receipt or configuration")
        descriptor = artifact_descriptor(self.identity)
        self.evidence.update(native_process={"pid": self.supervisor.proc.pid, "port": self.supervisor.port},
                             gateway_port=self.gateway.port, gateway_identity=self.identity, artifact=descriptor,
                             planner={"runtime": PLANNER_RUNTIME, "artifact_sha256": canonical_digest(descriptor),
                                      "protocol_sha256": PLANNER_PROTOCOL_SHA256})

    def healthy(self, *, fresh: bool) -> bool:
        if not self.accepting or self.stop_requested.is_set():
            return False
        if (self.gateway.mode != "production" or self.gateway.needs_restart
                or self.gateway.thread is None or not self.gateway.thread.is_alive()
                or self.supervisor.state() != "running"):
            self.fail("owned_runtime_lost")
            return False
        if fresh:
            try:
                if validate_gateway_identity(self.backend.inspect()) != self.identity:
                    raise ValueError("runtime changed")
            except Exception:
                self.fail("owned_runtime_identity_unavailable")
                return False
        # A concurrent shutdown or identity failure must win over a successful probe.
        return self.accepting and not self.stop_requested.is_set()

    def application(self, manifest: dict, authorization: dict):
        if manifest["planner"] != self.evidence["planner"]:
            raise ValueError("loaded planner artifact differs from immutable manifest")
        # A release declares placement; it cannot establish the observed network topology.
        self.evidence["declared_placement"] = dict(manifest["placement"])
        app = create_app(manifest, self.backend, **authorization)

        @app.middleware("http")
        async def owning_lifetime(request, call_next):
            if request.url.path != "/ready" and not self.healthy(fresh=False):
                return JSONResponse({"detail": "owned planner unavailable"}, status_code=503)
            response = await call_next(request)
            # Fence an in-flight successful response if shutdown/runtime loss won.
            if request.url.path != "/ready" and not self.healthy(fresh=False):
                return JSONResponse({"detail": "owned planner unavailable"}, status_code=503)
            return response

        @app.get("/ready")
        def ready():
            if not self.healthy(fresh=True):
                raise HTTPException(503, "owned planner unavailable")
            return {"ready": True, "release_digest": canonical_digest(manifest),
                    "planner_artifact_sha256": self.evidence["planner"]["artifact_sha256"]}

        return app

    def serve(self, app, host: str, port: int):
        import uvicorn

        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind((host, port))
        self.listener.listen(16)
        self.server = uvicorn.Server(uvicorn.Config(
            app, log_level="warning", access_log=False, limit_concurrency=16,
            timeout_graceful_shutdown=max(1, self.stop_timeout_s / 2), timeout_keep_alive=2,
        ))
        self.server_thread = threading.Thread(
            target=self.server.run, kwargs={"sockets": [self.listener]}, name="convoy-owned-planner", daemon=True,
        )
        self.server_thread.start()
        while not self.server.started:
            if not self.server_thread.is_alive():
                raise RuntimeError("planner listener failed to start")
            time.sleep(0.02)
        self.accepting = True
        self.evidence.update(status="ready", planner_port=self.listener.getsockname()[1])

    def monitor(self):
        while not self.stop_requested.wait(0.5):
            if not self.server_thread.is_alive():
                self.fail("planner_listener_lost")
            if not self.healthy(fresh=True):
                break

    def close(self) -> dict:
        self.starting = False
        self.accepting = False
        self.stop_requested.set()
        deadline = time.monotonic() + self.stop_timeout_s
        result = {"native_stopped": False, "gateway_closed": False,
                  "gateway_drained": False, "planner_closed": False,
                  "native_marker_removed": False, "listeners_closed": False}
        marker = getattr(self.supervisor, "api_key_file", None)
        ports = [getattr(self.supervisor, "port", 0), getattr(self.gateway, "port", 0)]
        if self.listener is not None and self.listener.fileno() >= 0:
            ports.append(self.listener.getsockname()[1])
        self.gateway.set_mode("closed")
        if self.server is not None:
            self.server.should_exit = True
        # Even a partially started Gateway must not block verified native cleanup.
        gateway_closed = threading.Event()
        def stop_gateway():
            try:
                if (self.gateway.server is not None
                        and (self.gateway.thread is None or self.gateway.thread.ident is None)):
                    self.gateway.server.server_close()
                    self.gateway.server = None
                else:
                    self.gateway.stop()
                if self.gateway.server is None:
                    gateway_closed.set()
            except Exception:
                pass
        closer = None
        try:
            closer = threading.Thread(target=stop_gateway, name="convoy-gateway-stop", daemon=True)
            closer.start()
        except Exception:
            # Resource exhaustion may also have caused startup to fail. Native
            # termination must still run if another cleanup thread cannot start.
            pass
        try:
            # Reserve some of the total budget to drain HTTP handlers after native exit.
            native = self.supervisor.stop(deadline=deadline - min(2, self.stop_timeout_s / 4))
            result["native_stopped"] = native.get("stopped") is True
            # Keep only bounded supervisor facts, never exception messages,
            # argv, paths or credentials. A stopped ownership record alone is
            # weaker than the supervisor's retained-child/reader checks.
            detail = {"stopped": result["native_stopped"]}
            for key in ("method", "reason"):
                value = native.get(key)
                if isinstance(value, str) and 0 < len(value) <= 128 and all(
                        char.isascii() and (char.isalnum() or char == "_") for char in value):
                    detail[key] = value
            seconds = native.get("seconds")
            if type(seconds) in (int, float) and math.isfinite(seconds) and 0 <= seconds <= 180:
                detail["seconds"] = seconds
            if type(native.get("exit_code")) is int and -(2**31) <= native["exit_code"] < 2**31:
                detail["exit_code"] = native["exit_code"]
            self.evidence["native_stop"] = detail
        except Exception as error:
            self.evidence["native_stop"] = {"stopped": False, "error_type": type(error).__name__[:128]}
        if closer is not None and closer.ident is not None:
            closer.join(max(0, deadline - time.monotonic()))
        result["gateway_closed"] = (gateway_closed.is_set() and closer is not None
                                    and not closer.is_alive())
        if self.gateway.thread is not None and self.gateway.thread.ident is not None:
            self.gateway.thread.join(max(0, deadline - time.monotonic()))
            result["gateway_closed"] = result["gateway_closed"] and not self.gateway.thread.is_alive()
        try:
            result["gateway_drained"] = self.gateway.drain(max(0, deadline - time.monotonic()))
        except Exception:
            pass
        if self.server_thread is not None and self.server_thread.ident is not None:
            self.server_thread.join(max(0, deadline - time.monotonic()))
        listener_closed = True
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                listener_closed = False
        result["planner_closed"] = listener_closed and (self.server_thread is None or not self.server_thread.is_alive())
        if self.server is not None and not result["planner_closed"]:
            self.server.force_exit = True
        try:
            result["native_marker_removed"] = marker is None or not marker.exists()
        except OSError:
            pass
        result["listeners_closed"] = True
        for port in ports:
            if port:
                try:
                    with socket.socket() as probe:
                        probe.settimeout(min(0.1, max(0.001, deadline - time.monotonic())))
                        if probe.connect_ex(("127.0.0.1", port)) == 0:
                            result["listeners_closed"] = False
                except OSError:
                    result["listeners_closed"] = False
        self.evidence["cleanup"] = result
        if not all(result.values()):
            self.failure = "cleanup_unresolved"
        return result

    def write(self, filename: str):
        data = json.dumps(self.evidence, indent=2, allow_nan=False) + "\n"
        if len(data.encode()) > 65536:
            raise ValueError("launcher evidence exceeds its bound")
        path = self.output / filename
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(data)


def run(args) -> int:
    receipt, manifest, authorization = preflight(args)
    output = args.output.resolve()
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    with OwnedProcess(output / "native" / "owner") as owner:
        lifetime = OwnedPlanner(receipt, output, owner, ctx_size=args.ctx_size, stop_timeout_s=args.stop_timeout_s,
                                threads=args.threads, threads_batch=args.threads_batch)
        previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM)}
        def timeout(*_):
            raise StartupTimeout()
        try:
            signal.signal(signal.SIGTERM, lifetime.signal_stop)
            signal.signal(signal.SIGINT, lifetime.signal_stop)
            signal.signal(signal.SIGALRM, timeout)
            signal.setitimer(signal.ITIMER_REAL, args.startup_timeout_s)
            lifetime.start(args.startup_timeout_s)
            if args.mode == "serve":
                lifetime.serve(lifetime.application(manifest, authorization), args.host, args.port)
            else:
                lifetime.evidence["status"] = "inspected"
            lifetime.starting = False
            signal.setitimer(signal.ITIMER_REAL, 0)
            lifetime.write("ready.json")
            if args.mode == "serve":
                lifetime.monitor()
        except StartupCancelled:
            lifetime.evidence["status"] = "interrupted"
        except (Exception, StartupTimeout) as error:
            lifetime.fail("startup_timeout" if isinstance(error, StartupTimeout) else "owned_planner_failed")
            lifetime.evidence["error_type"] = type(error).__name__
        finally:
            lifetime.starting = False
            signal.setitimer(signal.ITIMER_REAL, 0)
            try:
                lifetime.close()
                if lifetime.failure:
                    lifetime.evidence.update(status="failed", failure=lifetime.failure)
                elif lifetime.evidence["status"] != "interrupted":
                    lifetime.evidence["status"] = "stopped"
                lifetime.write("result.json")
            finally:
                for number, handler in previous.items():
                    signal.signal(number, handler)
        return 1 if lifetime.failure else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("inspect", "serve"))
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new private runtime directory")
    parser.add_argument("--ctx-size", type=int, choices=(2048, 4096), default=2048)
    parser.add_argument("--threads", type=int, choices=range(1, 257), default=2,
                        help="explicit generation CPU threads, fingerprinted in the planner artifact (default: 2)")
    parser.add_argument("--threads-batch", type=int, choices=range(1, 257), default=2,
                        help="explicit prompt/batch CPU threads, fingerprinted in the planner artifact (default: 2)")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--host", choices=("127.0.0.1", "0.0.0.0"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9101)
    parser.add_argument("--startup-timeout-s", type=int, choices=range(1, 121), default=120)
    parser.add_argument("--stop-timeout-s", type=int, choices=range(1, 31), default=15)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("invalid planner port")
    try:
        return run(args)
    except (ValueError, OSError):
        # Do not print file paths, credentials or untrusted receipt contents.
        parser.exit(1, "owned planner configuration or local state rejected\n")


if __name__ == "__main__":
    raise SystemExit(main())
