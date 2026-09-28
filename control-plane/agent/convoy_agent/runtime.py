"""Runtime supervisor: exactly one owned child (real llama-server or the in-process simulator).

* argv from `runtime_args.argv_for` (single source of truth), environment scrubbed of LLAMA_ARG_*
* per-launch random API key in a 0600 file passed via --api-key-file; never logged or reported
* health = /health 200 within timeout; backend evidence from /props + startup log lines
* stop verifies the child exited (SIGTERM, then SIGKILL) before returning
* only a child this supervisor PROVABLY owns is ever signalled (R39): ownership is recorded durably in
  `<workdir>/child.json` (pid, kernel start time from /proc/<pid>/stat field 22, the per-launch
  api-key-file path that only our argv carries). A new supervisor over the same workdir verifies the
  recorded identity (same start time AND our marker in /proc/<pid>/cmdline) before terminating and
  reaping an orphan left by a SIGKILLed agent; anything else only discards the record. systemd's
  cgroup cleanup (KillMode=mixed) is an additional layer on the Jetson, not the only one: this
  mechanism also covers agents run outside systemd and a crashed agent that systemd restarts.
* the child's output is drained by a reader thread into bounded memory (first K KiB of startup lines
  for backend evidence + a ring of the last N KiB) and a size-capped per-launch file (R29-fu); the
  on-disk footprint stays bounded however chatty the runtime is
* opt-in `process_owner` replaces the legacy Linux ownership path with a caller-held OwnedProcess
  lock and verified Darwin/Linux recovery. Only a known single foreground child is supported;
  unresolved legacy records block migration. See docs/v1/owned-process-recovery.md."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import signal
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from typing import Any, Callable

from .runtime_args import argv_for, canonical_config, scrub_env

log = logging.getLogger("convoy.agent.runtime")
_OFFLOAD_RE = re.compile(r"offloaded (\d+)/(\d+) layers to GPU")
_BACKEND_RE = re.compile(r"\b(CUDA\d+|Metal|CPU(?:_Mapped|_REPACK)?|Vulkan\d*)\b")
_DEVICE_RE = re.compile(r"ggml_cuda_init: found (\d+) CUDA devices")
CHILD_RECORD = "child.json"
# Child identity proof (pid + kernel start time + cmdline marker) is a Linux /proc contract; the Jetson
# target is Linux. Elsewhere ownership records are discarded without signalling anything.
PROC_IDENTITY_AVAILABLE = os.path.isdir("/proc/self") and os.path.exists("/proc/self/stat")
LOG_HEAD_BYTES = 64 * 1024  # startup lines retained verbatim (backend evidence lives here)
LOG_RING_BYTES = 256 * 1024  # rolling tail retained in memory
LOG_FILE_CAP_BYTES = 1024 * 1024  # hard cap of the per-launch on-disk log (rewritten as head + tail)
_MAX_LINE = 64 * 1024


def proc_start_ticks(pid: int) -> str | None:
    """Kernel start time (clock ticks since boot) of `pid`, field 22 of /proc/<pid>/stat; None when the
    process does not exist. Together with the pid this identifies one process incarnation."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    tail = raw[raw.rfind(")") + 2 :].split()  # fields from 3 (state) onwards; comm may contain spaces
    if len(tail) < 20:
        return None
    return tail[19]  # field 22 (starttime), 0-based offset 19 after 'state'


def proc_rss_mb(pid: int) -> float | None:
    """Resident set of `pid` from /proc/<pid>/status VmRSS, in MiB; None when unavailable."""
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    except (OSError, ValueError, IndexError):
        return None
    return None


def proc_state(pid: int) -> str | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    tail = raw[raw.rfind(")") + 2 :].split()
    return tail[0] if tail else None


def proc_pgrp(pid: int) -> int | None:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    tail = raw[raw.rfind(")") + 2 :].split()
    try:
        return int(tail[2])  # field 5 (pgrp)
    except (IndexError, ValueError):
        return None


def proc_cmdline(pid: int) -> list[str] | None:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [x.decode("utf-8", "replace") for x in raw.split(b"\0") if x]


class _LogReader(threading.Thread):
    """Drains the child's stdout/stderr pipe. Keeps the first LOG_HEAD_BYTES of lines and a ring of the
    last LOG_RING_BYTES in memory, and writes a per-launch file that is rewritten as head + tail
    whenever it would exceed LOG_FILE_CAP_BYTES, so continuous chatter never grows the disk footprint."""

    def __init__(self, pipe, path: Path, *, head_bytes: int, ring_bytes: int, file_cap: int):
        super().__init__(name="convoy-runtime-log", daemon=True)
        self.pipe = pipe
        self.path = path
        self.head_bytes, self.ring_bytes, self.file_cap = head_bytes, ring_bytes, file_cap
        self.head: list[bytes] = []
        self.head_n = 0
        self.ring: deque[bytes] = deque()
        self.ring_n = 0
        self.total_bytes = 0
        self.rotations = 0
        self.file_bytes = 0
        self.lock = threading.Lock()

    def run(self) -> None:
        try:
            with open(self.path, "wb") as f:
                while True:
                    try:
                        line = self.pipe.readline(_MAX_LINE)
                    except (OSError, ValueError):
                        break
                    if not line:
                        break
                    self._ingest(line, f)
                f.flush()
        except OSError as e:
            log.warning("runtime log file unavailable (%s); draining to memory only", e)
            while True:
                try:
                    line = self.pipe.readline(_MAX_LINE)
                except (OSError, ValueError):
                    break
                if not line:
                    break
                self._ingest(line, None)
        finally:
            try:
                self.pipe.close()
            except OSError:
                pass

    def _ingest(self, line: bytes, f) -> None:
        n = len(line)
        with self.lock:
            self.total_bytes += n
            if self.head_n < self.head_bytes:
                self.head.append(line)
                self.head_n += n
            else:
                self.ring.append(line)
                self.ring_n += n
                while self.ring_n > self.ring_bytes and self.ring:
                    self.ring_n -= len(self.ring.popleft())
            if f is None:
                return
            if self.file_bytes + n > self.file_cap:
                f.seek(0)
                f.truncate()
                marker = b"...[convoy: log rotated in place; head + last lines retained]...\n"
                for x in self.head:
                    f.write(x)
                f.write(marker)
                for x in self.ring:
                    f.write(x)
                self.file_bytes = self.head_n + len(marker) + self.ring_n
                self.rotations += 1
            else:
                f.write(line)
                self.file_bytes += n
            f.flush()

    def lines(self) -> list[bytes]:
        with self.lock:
            return list(self.head) + list(self.ring)


class RuntimeError_(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def http_json(
    url: str, api_key: str | None, body: Any = None, timeout: float = 10.0, method: str | None = None
) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw[:200].decode("utf-8", "replace")


class RuntimeSupervisor:
    def __init__(
        self, workdir: Path, *, simulate: bool = False, sensors=None, sim_faults: dict[str, Any] | None = None,
        process_owner=None,
    ):
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.simulate = simulate
        if process_owner is not None and simulate:
            raise ValueError("owned foreground processes require a real runtime")
        self.process_owner = process_owner
        self.sensors = sensors
        self.sim_faults = dict(sim_faults or {})
        self.proc: subprocess.Popen | None = None
        self._kill_sent = False  # a SIGKILL already sent to the current child without a verified exit
        self.sim = None
        self.port: int | None = None
        self.api_key: str | None = None
        self.api_key_file: Path | None = None
        self.release_id: str | None = None
        self.config: dict[str, Any] = {}
        self.argv: list[str] = []
        self.log_path: Path | None = None
        self.evidence: dict[str, Any] = {}
        self._provenance: dict[str, Any] = {}
        self.started_at: float | None = None  # monotonic instant of the current launch (self.clock)
        self.stopped_at: float | None = None  # monotonic instant the last child was verified stopped
        # every start/stop TRANSITION (never just the latest instants): [[start, stop|None], ...]
        self.run_intervals: list[list[float | None]] = []
        self.clock = time.monotonic  # injectable for accounting tests; never affects timeouts' meaning
        self._lock = threading.RLock()
        self.generation = 0  # increments on every start; gateway checks it across a request
        self._reader: _LogReader | None = None
        # R39 follow-up: constructing a supervisor proves nothing about ownership; orphan inspection and
        # signalling happen only once the caller holds exclusive ownership (Agent.run after the lock, or
        # start()), never at construction.
        self.orphan_note: dict[str, Any] = {"action": "not_inspected"}

    # ---- lifecycle ----
    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def _open_interval(self, t: float) -> None:
        if self.run_intervals and self.run_intervals[-1][1] is None:
            self.run_intervals[-1][1] = t  # a start without a recorded stop: close it at the new start
        self.run_intervals.append([t, None])
        if len(self.run_intervals) > 4096:
            del self.run_intervals[:-2048]

    def _close_interval(self, t: float) -> None:
        if self.run_intervals and self.run_intervals[-1][1] is None:
            self.run_intervals[-1][1] = t

    def up_time(self, since: float, until: float) -> float:
        """Seconds the child was running within (since, until], from the recorded transitions, so
        intervening start/stop pairs between two samples are never missed."""
        total = 0.0
        for start, stop in self.run_intervals:
            s0 = max(float(start), since)
            s1 = min(until if stop is None else float(stop), until)
            if s1 > s0:
                total += s1 - s0
        return total

    def footprint_mb(self) -> float | None:
        """Measured resident memory of the running child (None when no real child process exists, e.g.
        the in-process simulated runtime, or when /proc is not readable)."""
        try:
            if self.proc is None or self.proc.poll() is not None:
                return None
            return proc_rss_mb(self.proc.pid)
        except Exception:
            return None

    def state(self) -> str:
        with self._lock:
            if self.simulate:
                return (
                    "running"
                    if (self.sim and not self.sim.crashed)
                    else ("crashed" if self.sim else "stopped")
                )
            if self.proc is None:
                return "stopped"
            return "running" if self.proc.poll() is None else "crashed"

    def provenance(self) -> dict[str, Any]:
        """Snapshot recorded at launch, never read a new manifest during a request."""
        with self._lock:
            if self.state() != "running" or not self._provenance:
                raise RuntimeError_("RUNTIME_UNAVAILABLE", "loaded runtime provenance unavailable")
            return json.loads(json.dumps(self._provenance))

    def ownership_fingerprints(self) -> dict[str, str]:
        """Additional source/dependency identity for opt-in foreground ownership.

        Fingerprint the installed backend bytes, including its native extension;
        a version string alone would miss another platform's implementation.
        Legacy gateways retain their original two-source identity shape.
        """
        if self.process_owner is None:
            return {}
        from .owned_process import _psutil

        psutil = _psutil()
        package = Path(psutil.__file__).parent
        files = {
            str(path.relative_to(package)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(package.rglob("*"))
            if path.is_file() and path.suffix in {".py", ".so"}
        }
        if not any(name.endswith(".so") for name in files):
            raise RuntimeError_("OWNERSHIP_IDENTITY_MISSING", "native process identity backend unavailable")
        backend = json.dumps({"version": psutil.__version__, "files": files}, sort_keys=True,
                             separators=(",", ":")).encode()
        return {
            **{name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
               for name in ("owned_process.py", "runtime_args.py")},
            "psutil-7.2.2": hashlib.sha256(backend).hexdigest(),
        }

    def start(
        self,
        *,
        release_id: str,
        spec: dict[str, Any],
        model_path: Path,
        template_path: Path | None,
        binary: Path | None,
        lib_dir: Path | None,
        health_timeout_s: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self.state() == "running":
                raise RuntimeError_(
                    "RUNTIME_ALREADY_RUNNING", "a runtime child is already running; stop it first"
                )
            cfg = canonical_config(spec.get("config", {}))
            model_sha = None
            expected_sha = (spec.get("model", {}).get("file") or {}).get("sha256")
            # Historical low-level callers may not provide a concrete pin. Preserve
            # their lifecycle behavior but leave provenance incomplete: the paired
            # planner refuses it rather than inferring an identity from a filename.
            if not self.simulate and expected_sha is not None:
                digest = hashlib.sha256()
                with model_path.open("rb") as model_source:
                    for block in iter(lambda: model_source.read(1024 * 1024), b""):
                        digest.update(block)
                model_sha = digest.hexdigest()
                if model_sha != expected_sha:
                    raise RuntimeError_("MODEL_DIGEST_MISMATCH", "model bytes differ from pinned launch identity")
            if self.process_owner is not None:
                if binary is None or not binary.is_file():
                    raise RuntimeError_("RUNTIME_BINARY_MISSING", "llama-server binary not found in the runtime artifact")
                if self._reader is not None and self._reader.is_alive():
                    raise RuntimeError_("RUNTIME_DRAIN_PENDING", "previous native output has not finished draining")
                # Recover under the caller's lifetime ownership lock BEFORE changing
                # credentials or launching another native runtime.
                self.reap_orphan()
            # A rejected pin is not a launch: do not rotate credentials or begin
            # generation/uptime accounting until the model preflight succeeds.
            self.config = cfg
            self.release_id = release_id
            self.port = free_port()
            self.api_key = secrets.token_urlsafe(32)
            self.api_key_file = None
            if self.process_owner is None:
                self.api_key_file = self.workdir / "runtime.key"
                fd = os.open(str(self.api_key_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write(self.api_key)
                os.chmod(self.api_key_file, 0o600)
            self.generation += 1
            self.started_at = self.clock()
            self._open_interval(self.started_at)
            self.evidence = {}
            self._provenance = {}
            self._reader = None  # evidence and tails come only from THIS launch
            if self.simulate:
                from .simruntime import SimRuntime

                faults = {
                    **self.sim_faults,
                    **(spec.get("config", {}).get("sim") or {}),
                    **(spec.get("sim") or {}),
                }
                self.sim = SimRuntime(
                    api_key=self.api_key,
                    n_ctx=cfg["ctx_size"],
                    n_predict=cfg["n_predict"],
                    model_path=str(model_path),
                    template=(template_path.read_text() if template_path else None),
                    faults=faults,
                    sensors=self.sensors,
                )
                self.port = self.sim.start()
                if self.sensors is not None:
                    self.sensors.runtime_mb = float(faults.get("runtime_mb", 1180.0)) + float(
                        spec.get("model", {}).get("total_bytes", 0)
                    ) / (1024 * 1024) * float(faults.get("weights_resident_fraction", 1.0))
                self.argv = argv_for(
                    cfg,
                    model_path=str(model_path),
                    template_path=str(template_path) if template_path else None,
                    host="127.0.0.1",
                    port=self.port,
                    api_key_file=str(self.api_key_file),
                    binary="llama-server.sim",
                )
                self.log_path = None
            else:
                if binary is None or not binary.exists():
                    raise RuntimeError_(
                        "RUNTIME_BINARY_MISSING", "llama-server binary not found in the runtime artifact"
                    )
                def native_argv(key_path: str) -> list[str]:
                    self.api_key_file = Path(key_path)
                    if self.process_owner is not None:
                        # The unique ownership marker is also llama-server's private
                        # key-file argument. No unsupported native flag is introduced.
                        with self.api_key_file.open("w") as stream:
                            stream.write(self.api_key)
                            stream.flush()
                            os.fsync(stream.fileno())
                    self.argv = argv_for(
                        cfg, model_path=str(model_path),
                        template_path=str(template_path) if template_path else None,
                        host="127.0.0.1", port=self.port, api_key_file=key_path,
                        binary=str(binary.resolve() if self.process_owner is not None else binary),
                    )
                    return self.argv

                env = scrub_env(dict(os.environ))
                if lib_dir:
                    env["LD_LIBRARY_PATH"] = str(lib_dir) + (
                        ":" + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else ""
                    )
                self.log_path = self.workdir / f"runtime.{self.generation}.log"  # per-launch identity (R29)
                for old in sorted(self.workdir.glob("runtime.*.log"))[:-5]:
                    old.unlink(missing_ok=True)
                if self.process_owner is not None:
                    self.proc = self.process_owner.start(
                        native_argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        env=env, cwd=self.workdir,
                    )
                else:
                    self.reap_orphan()  # legacy Linux agent ownership remains unchanged
                    self.proc = subprocess.Popen(
                        native_argv(str(self.api_key_file)), stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, env=env, cwd=str(self.workdir),
                        start_new_session=True,
                    )
                self._kill_sent = False  # a new child: escalation state belongs to this launch
                if self.process_owner is None:
                    self._write_child_record(self.proc.pid)
                self._reader = _LogReader(
                    self.proc.stdout,
                    self.log_path,
                    head_bytes=LOG_HEAD_BYTES,
                    ring_bytes=LOG_RING_BYTES,
                    file_cap=LOG_FILE_CAP_BYTES,
                )
                self._reader.start()
            timeout = health_timeout_s or float(cfg.get("health_timeout_s", 120))
            ok, detail = self.wait_healthy(timeout)
            if not ok:
                self.stop()
                raise RuntimeError_(
                    "HEALTH_TIMEOUT" if detail.get("timeout") else "RUNTIME_START_FAILED",
                    f"runtime did not become healthy: {detail.get('last')}",
                    detail,
                )
            self.evidence = self.collect_evidence()
            self._provenance = {
                "release_id": release_id, "runtime_generation": self.generation,
                "model_sha256": model_sha,
                "runtime_artifact_sha256": spec.get("runtime", {}).get("artifact_sha256"),
                "binary_sha256": self.evidence.get("binary_sha256"),
                "template_sha256": self.evidence.get("chat_template_sha256"),
                "config_sha256": hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":"),
                                                            allow_nan=False).encode()).hexdigest(),
                "simulated": self.simulate,
            }
            return self.evidence

    def wait_healthy(self, timeout_s: float) -> tuple[bool, dict[str, Any]]:
        t0 = time.monotonic()
        last: Any = None
        while time.monotonic() - t0 < timeout_s:
            if self.state() != "running":
                return False, {
                    "last": "process exited",
                    "exit_code": self.proc.returncode if self.proc else None,
                    "log_tail": self.log_tail(),
                }
            try:
                code, body = http_json(f"{self.base_url}/health", None, timeout=3)
                last = (code, body)
                if code == 200 and (isinstance(body, dict) and body.get("status") == "ok"):
                    return True, {"seconds": round(time.monotonic() - t0, 3)}
            except (urllib.error.URLError, OSError, ConnectionError) as e:
                last = str(e)[:100]
            time.sleep(0.2)
        return False, {"timeout": True, "last": last, "log_tail": self.log_tail()}

    def health(self) -> bool:
        if self.state() != "running":
            return False
        try:
            code, body = http_json(f"{self.base_url}/health", None, timeout=3)
            return code == 200
        except Exception:
            return False

    def props(self) -> dict[str, Any] | None:
        try:
            code, body = http_json(f"{self.base_url}/props", self.api_key, timeout=5)
            return body if code == 200 and isinstance(body, dict) else None
        except Exception:
            return None

    def slots_idle(self) -> bool | None:
        try:
            code, body = http_json(f"{self.base_url}/slots", self.api_key, timeout=3)
            if code == 200 and isinstance(body, list):
                return not any(s.get("is_processing") for s in body)
        except Exception:
            return None
        return None

    def log_lines(self) -> list[str]:
        """Startup head + rolling tail of THIS launch, from the reader's bounded memory (redacted)."""
        if self._reader is None:
            return []
        return [_redact_line(x.decode("utf-8", "replace").rstrip("\r\n")) for x in self._reader.lines()]

    def log_stats(self) -> dict[str, Any]:
        r = self._reader
        if r is None:
            return {"total_bytes": 0, "rotations": 0, "file_bytes": 0, "retained_bytes": 0}
        with r.lock:
            return {
                "total_bytes": r.total_bytes,
                "rotations": r.rotations,
                "file_bytes": r.file_bytes,
                "retained_bytes": r.head_n + r.ring_n,
            }

    def log_tail(self, n: int = 40, max_bytes: int = 256 * 1024) -> list[str]:
        """Bounded tail of THIS launch's log (never a previous launch's)."""
        if self._reader is not None:
            out: list[str] = []
            total = 0
            for line in reversed(self._reader.lines()):
                if len(out) >= n or total + len(line) > max_bytes:
                    break
                out.append(_redact_line(line.decode("utf-8", "replace").rstrip("\r\n")))
                total += len(line)
            out.reverse()
            return out
        if not self.log_path or not self.log_path.exists():
            return []
        try:
            with open(self.log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                data = f.read()
        except OSError:
            return []
        lines = data.decode("utf-8", errors="replace").splitlines()
        return [_redact_line(x) for x in lines[-n:]]

    # ---- child ownership (R39) ----
    def _record_path(self) -> Path:
        return self.workdir / CHILD_RECORD

    def _write_child_record(self, pid: int) -> None:
        rec = {
            "pid": pid,
            "start_ticks": proc_start_ticks(pid),
            "marker": str(self.api_key_file),
            "argv0": self.argv[0] if self.argv else None,
            "generation": self.generation,
            "launched_at": time.time(),
        }
        tmp = self._record_path().with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(rec, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._record_path())

    def _clear_child_record(self) -> None:
        try:
            self._record_path().unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _record_matches_live_process(self, rec: dict[str, Any]) -> tuple[bool, str]:
        pid = rec.get("pid")
        if not isinstance(pid, int) or pid <= 1:
            return False, "no pid"
        if not PROC_IDENTITY_AVAILABLE:
            # identity cannot be proven without /proc (Linux); the record is discarded, nothing is signalled
            return False, "no /proc identity on this platform"
        state = proc_state(pid)
        if state is None:
            return False, "pid gone"
        if state == "Z":
            return False, "zombie"
        if proc_start_ticks(pid) != rec.get("start_ticks") or not rec.get("start_ticks"):
            return False, "start time differs (pid reused)"
        cmd = proc_cmdline(pid) or []
        marker = rec.get("marker")
        if not marker or marker not in cmd:
            return False, "marker not in cmdline"
        return True, "verified"

    def reap_orphan(self, timeout_s: float = 20.0) -> dict[str, Any]:
        """If a previous supervisor over this workdir left a child record, terminate that child only
        after proving its identity (same pid + start time AND our api-key-file marker in its cmdline);
        otherwise just discard the record. Never signals an unrelated pid."""
        path = self._record_path()
        if self.process_owner is not None:
            # The old Linux-only record is not proof under the new backend. Do not
            # discard it, adopt it, or silently start alongside a possible orphan.
            if path.exists():
                raise RuntimeError_("LEGACY_OWNERSHIP_UNRESOLVED", "legacy native ownership needs explicit recovery")
            self.orphan_note = self.process_owner.recover(
                terminate_timeout=min(30.0, timeout_s), kill_timeout=3.0,
            )
            return self.orphan_note
        if not path.exists():
            return {"action": "none"}
        try:
            rec = json.loads(path.read_text())
        except (OSError, ValueError):
            self._clear_child_record()
            return {"action": "discarded", "reason": "unreadable record"}
        ok, why = self._record_matches_live_process(rec)
        pid = rec.get("pid")
        self.orphan_note = {"action": "inspected", "pid": pid, "verified": ok, "reason": why}
        if not ok:
            if why == "zombie" and isinstance(pid, int):
                try:
                    os.waitpid(pid, os.WNOHANG)  # reap only if it happens to be our own child
                except ChildProcessError:
                    pass
            self._clear_child_record()
            self.orphan_note = {"action": "discarded", "reason": why, "pid": pid}
            return self.orphan_note
        method = "sigterm"
        try:
            if proc_pgrp(pid) == pid:
                os.killpg(pid, signal.SIGTERM)
            else:
                os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError:
            self._clear_child_record()
            return {"action": "discarded", "reason": "permission denied", "pid": pid}
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s and self._record_matches_live_process(rec)[0]:
            time.sleep(0.05)
        if self._record_matches_live_process(rec)[0]:
            method = "sigkill"
            try:
                if proc_pgrp(pid) == pid:
                    os.killpg(pid, signal.SIGKILL)
                else:
                    os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            t0 = time.monotonic()
            while time.monotonic() - t0 < 10 and self._record_matches_live_process(rec)[0]:
                time.sleep(0.05)
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass  # not our child: init/subreaper reaps it
        gone = not self._record_matches_live_process(rec)[0]
        self._clear_child_record()
        note = {"action": "terminated" if gone else "unverified_exit", "pid": pid, "method": method}
        self.orphan_note = note
        log.warning("orphaned runtime child from a previous agent: %s", note)
        return note

    def collect_evidence(self) -> dict[str, Any]:
        """Backend evidence: props build_info + startup log offload lines + binary hash. Sensor/nvidia-smi
        presence is not inference proof; the offloaded-layer line and props are."""
        props = self.props() or {}
        ev: dict[str, Any] = {
            "build_info": props.get("build_info"),
            "model_path": props.get("model_path"),
            "n_ctx": props.get("n_ctx"),
            "total_slots": props.get("total_slots"),
            "chat_template_sha256": hashlib.sha256((props.get("chat_template") or "").encode()).hexdigest()
            if props.get("chat_template")
            else None,
            "binary_sha256": None,
            "gpu_offloaded_layers": None,
            "gpu_total_layers": None,
            "backend": None,
            "cuda_devices": None,
            "intended_backend_ok": None,
            "argv": [a for a in self.argv if not a.endswith("runtime.key")],
            "simulated": self.simulate,
        }
        if self.simulate:
            ev.update(
                {
                    "binary_sha256": hashlib.sha256(b"convoy-simulated-runtime").hexdigest(),
                    "backend": "simulated",
                    "gpu_offloaded_layers": None,
                    "intended_backend_ok": None,
                    "note": "simulated runtime: no GPU evidence exists and none is claimed",
                }
            )
            return ev
        try:
            data = Path(self.argv[0]).read_bytes()
            ev["binary_sha256"] = hashlib.sha256(data).hexdigest()
        except OSError:
            pass
        for line in self.log_lines():
            m = _OFFLOAD_RE.search(line)
            if m:
                ev["gpu_offloaded_layers"], ev["gpu_total_layers"] = int(m.group(1)), int(m.group(2))
            m = _DEVICE_RE.search(line)
            if m:
                ev["cuda_devices"] = int(m.group(1))
            if "load_tensors:" in line:
                b = _BACKEND_RE.search(line)
                if b:
                    backend = b.group(1)
                    ev["backend"] = "CPU" if backend in {"CPU_Mapped", "CPU_REPACK"} else backend
        if ev["gpu_offloaded_layers"] is not None and ev["gpu_total_layers"]:
            ev["intended_backend_ok"] = ev["gpu_offloaded_layers"] == ev["gpu_total_layers"] and (
                ev["backend"] or ""
            ).startswith("CUDA")
        elif ev["backend"] == "CPU":
            ev["intended_backend_ok"] = False
        return ev

    def stop(self, timeout_s: float = 20.0, *, deadline: float | None = None) -> dict[str, Any]:
        """Stop and VERIFY exit. Returns {stopped: bool, seconds, method}. `deadline` (monotonic) is ONE
        absolute bound shared by every wait in here: the supervisor lock (a start in progress holds it
        through the health wait), the SIGTERM grace, the SIGKILL reap and the reader join are each cut
        to what remains of it, so the sum never exceeds it. When the lock cannot be taken in time nothing
        is touched and {stopped: False, method: "busy"} is returned; when the child does not reap in
        time {stopped: False} is returned rather than blocking past the deadline."""

        def left() -> float | None:
            return None if deadline is None else max(0.0, deadline - time.monotonic())

        if deadline is not None:
            if not self._lock.acquire(timeout=left() or 0.0):
                return {"stopped": False, "seconds": 0.0, "method": "busy"}
        else:
            self._lock.acquire()
        try:
            return self._stop_locked(timeout_s, left)
        finally:
            self._lock.release()

    def _stop_locked(self, timeout_s: float, left: Callable[[], float | None]) -> dict[str, Any]:
        def cut(want: float, floor: float = 0.05) -> float:
            rem = left()
            return want if rem is None else max(floor, min(want, rem))

        if True:
            t0 = time.monotonic()
            if self.process_owner is not None:
                from .owned_process import OwnershipError

                if self._record_path().exists():
                    return {"stopped": False, "seconds": round(time.monotonic() - t0, 3),
                            "method": "owned_process", "reason": "legacy_ownership_unresolved"}
                remaining = left()
                budget = min(30.0, timeout_s) + 3.0 if remaining is None else remaining
                kill_window = min(3.0, budget / 2)
                try:
                    result = self.process_owner.stop(
                        terminate_timeout=min(30.0, max(0.0, budget - kill_window)),
                        kill_timeout=kill_window,
                    )
                except OwnershipError as error:
                    return {"stopped": False, "seconds": round(time.monotonic() - t0, 3),
                            "method": "owned_process", "reason": error.code}
                rc = self.proc.poll() if self.proc is not None else None
                if self.proc is not None and rc is None:
                    # A missing record is not stronger evidence than our live
                    # child handle. Keep its credentials and output ownership.
                    return {"stopped": False, "seconds": round(time.monotonic() - t0, 3),
                            "method": "owned_process", "reason": "child_exit_unverified",
                            "pid": self.proc.pid}
                if self._reader is not None:
                    self._reader.join(timeout=5.0 if left() is None else left())
                    if self._reader.is_alive():
                        return {"stopped": False, "seconds": round(time.monotonic() - t0, 3),
                                "method": "owned_process", "reason": "output_drain_incomplete",
                                "pid": result.get("pid")}
                self.proc = None
                self.stopped_at = self.clock()
                self._close_interval(self.stopped_at)
                self._cleanup_key()
                return {"stopped": True, "seconds": round(time.monotonic() - t0, 3),
                        "method": "owned_process", "exit_code": rc, "pid": result.get("pid")}
            if self.simulate:
                if self.sim:
                    self.sim.stop()
                    self.sim = None
                if self.sensors is not None:
                    self.sensors.runtime_mb = 0.0
                    self.sensors.load = 0.0
                self._cleanup_key()
                if self.started_at is not None:
                    self.stopped_at = self.clock()
                    self._close_interval(self.stopped_at)
                return {"stopped": True, "seconds": round(time.monotonic() - t0, 3), "method": "sim"}
            if self.proc is None:
                self._cleanup_key()
                return {"stopped": True, "seconds": 0.0, "method": "none"}
            pid = self.proc.pid
            method = "sigterm"
            try:
                if self.proc.poll() is None:
                    if self._kill_sent:
                        # a previous stop already escalated to SIGKILL and could not verify the exit:
                        # retry the kill for the SAME child rather than starting over with SIGTERM
                        method = "sigkill"
                        os.killpg(pid, signal.SIGKILL)
                        try:
                            self.proc.wait(timeout=cut(10.0))
                        except subprocess.TimeoutExpired:
                            pass
                    else:
                        os.killpg(pid, signal.SIGTERM)
                        try:
                            # keep at least a short reap window for SIGKILL inside the same deadline
                            self.proc.wait(
                                timeout=cut(timeout_s) if left() is None else max(0.05, cut(timeout_s) - 0.5)
                            )
                        except subprocess.TimeoutExpired:
                            method = "sigkill"
                            self._kill_sent = True
                            os.killpg(pid, signal.SIGKILL)
                            try:
                                self.proc.wait(timeout=cut(10.0))
                            except subprocess.TimeoutExpired:
                                pass  # reported below as not stopped; never blocks past the deadline
            except ProcessLookupError:
                pass
            stopped = self.proc.poll() is not None
            rc = self.proc.returncode
            if not stopped:
                # the exit is NOT verified: the child, its record, its key file and its log reader stay
                # owned exactly as they are, so a later stop retries and reports this same child instead
                # of inventing "none"; the caller keeps ownership (shutdown incomplete)
                return {
                    "stopped": False,
                    "seconds": round(time.monotonic() - t0, 3),
                    "method": method,
                    "exit_code": None,
                    "pid": pid,
                }
            self.proc = None
            self._kill_sent = False
            self._clear_child_record()
            self.stopped_at = self.clock()
            self._close_interval(self.stopped_at)
            if self._reader is not None:
                self._reader.join(timeout=cut(5.0))  # EOF once the child (and its group) exit
            self._cleanup_key()
            return {
                "stopped": True,
                "seconds": round(time.monotonic() - t0, 3),
                "method": method,
                "exit_code": rc,
                "pid": pid,
            }

    def _cleanup_key(self) -> None:
        if self.api_key_file and self.api_key_file.exists():
            try:
                self.api_key_file.unlink()
            except OSError:
                pass
        self.api_key = None


def _redact_line(line: str) -> str:
    return re.sub(r"(api[-_ ]?key\S*\s*[:=]?\s*)\S+", r"\1[redacted]", line, flags=re.I)
