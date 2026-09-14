"""Agent main loop: singleton lock, restart recovery, live reports with server nonce echo, operation
execution in a worker thread, spool flush with server-committed cursors, optional simulated robot."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import signal
import threading
import time
from pathlib import Path
from typing import Any

from . import __version__, hardware
from .client import ApiError, Client, Transient
from .executor import Executor, OpFailure
from .gateway import Gateway
from .ids import now_iso
from .journal import LANES, Journal, StorageBusy, StorageError
from .runtime import RuntimeError_, RuntimeSupervisor

log = logging.getLogger("convoy.agent")
USAGE_CUM_KEYS = (
    "requests",
    "tokens_in",
    "tokens_out",
    "inference_s",
    "runtime_up_s",
    "agent_up_s",
    "connected_s",
    "contacts",
    "reconnects",
)
USAGE_CKPT_REQUESTS = 25
# Usage record schema. Unversioned records (no `schema` key) came from agents whose `active_minutes` /
# `online_minutes` / `connected_minutes` meant different things over time; the server keeps them under
# explicit "mixed" names. Schema 2 records carry the measured populations under distinct names
# (`inference_minutes`, `contact_minutes`, ...) and are never mixed with them.
USAGE_SCHEMA = 2
# Durable usage checkpoint version. Checkpoints written before the accounting rework carry neither
# `schema` nor `incarnation`; their durations were sampled with the old semantics, so on recovery only
# their integer deltas are measured facts and the residual durations are classified like unversioned
# ingress (mixed, not attributable), never promoted to the measured populations.
USAGE_CKPT_SCHEMA = 2


def legacy_checkpoint_recovery_record(ckpt: dict[str, Any], now_wall: float) -> dict[str, Any]:
    """Recovery record for a pre-rework checkpoint (no `schema`, no `incarnation`): a schema-2 record
    whose invariant integer deltas (requests, tokens) are emitted exactly once, whose residual
    durations are declared under the explicit MIXED names with `legacy_checkpoint: true` (the server
    accepts mixed names under schema 2 only with that provenance flag), and whose unknown coverage
    runs from the checkpoint's wall time. `runtime_up_s`/`agent_up_s` residuals are not attributable
    to any population the legacy ingress kept and are dropped, as that ingress dropped them."""
    cum, emitted = ckpt.get("cum") or {}, ckpt.get("emitted") or {}

    def delta(k: str) -> float:
        return max(0.0, float(cum.get(k, 0.0)) - float(emitted.get(k, 0.0)))

    from_ts = float(ckpt.get("wall") or now_wall)
    open_from = ckpt.get("open_interval_from_wall")
    if open_from is not None:
        from_ts = min(from_ts, float(open_from))
    unknown = max(0.0, now_wall - from_ts)
    return {
        "schema": USAGE_SCHEMA,
        "ts": now_wall,
        "interval_s": 0.0,
        "inference_requests": int(delta("requests")),
        "tokens_in": int(delta("tokens_in")),
        "tokens_out": int(delta("tokens_out")),
        "mixed_active_minutes": round(delta("inference_s") / 60.0, 4),
        "mixed_online_minutes": round(delta("connected_s") / 60.0, 4),
        "legacy_checkpoint": True,
        "recovered_after_restart": True,
        "previous_boot_id": ckpt.get("boot_id"),
        "previous_incarnation": None,
        "unknown_coverage_s": round(unknown, 3),
        "unknown_interval": {
            "from_ts": from_ts,
            "to_ts": now_wall,
            "reason": "restart_before_durable_checkpoint",
        },
    }


# Bounded shutdown (systemd TimeoutStopSec=45 s, KillMode=mixed): one monotonic budget from the stop
# request, with a 10 s margin before the unit's SIGKILL. Shares are upper bounds, spent only when the
# corresponding part is slow; the idle case (no operation, retained runtime, dead control plane) ends
# as soon as the in-flight report attempt is aborted and the child has stopped.
SHUTDOWN_BUDGET_S = 35.0
SHUTDOWN_WORKER_S = 20.0  # cancelled operation thread (its control-plane I/O is stop-aware)
SHUTDOWN_DRAIN_S = 5.0  # gateway handlers still running after the child stopped
SHUTDOWN_RESERVE_S = 4.0  # checkpointer join, final usage record, journal close, lock release
CHAT_POLL_INTERVAL_S = 1.0
SHUTDOWN_WAIT_SLICE_S = 0.5  # the loop's poll wait granularity (a signal-set flag is seen this fast)

STAGE_MAP = {
    "Staging": "staging",
    "WaitingGrant": "waiting_grant",
    "Cutover": "cutover",
    "Evaluating": "evaluating",
    "Probation": "probation",
    "Recovering": "recovering",
}


class AgentConfig:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "agent.json"
        self.cred_path = self.data_dir / "credential"
        self.data: dict[str, Any] = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        os.replace(tmp, self.path)

    def secret(self) -> str | None:
        return self.cred_path.read_text().strip() if self.cred_path.exists() else None

    def write_secret(self, secret: str) -> None:
        fd = os.open(str(self.cred_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secret)
        os.chmod(self.cred_path, 0o600)

    @property
    def credential(self) -> str | None:
        s = self.secret()
        d = self.data.get("device_id")
        return f"cvd_{d}_{s}" if (s and d) else None


def enroll(
    data_dir: Path,
    *,
    server: str,
    token: str,
    name: str,
    simulate: bool,
    seed: int = 1,
    ca_file: str | None = None,
    insecure: bool = False,
) -> dict[str, Any]:
    """Hash-commitment enrollment: generate the secret locally, persist it first, then claim."""
    import hashlib

    cfg = AgentConfig(data_dir)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    secret = cfg.secret()
    if not secret or cfg.data.get("enroll_request_id") is None or cfg.data.get("device_id"):
        secret = secrets.token_urlsafe(32)
        cfg.write_secret(secret)
        cfg.data["enroll_request_id"] = "req-" + secrets.token_hex(8)
        cfg.data.pop("device_id", None)
    cfg.data.update(
        {
            "server": server.rstrip("/"),
            "name": name,
            "simulate": simulate,
            "seed": seed,
            "ca_file": ca_file,
            "insecure": insecure,
        }
    )
    cfg.save()  # persisted BEFORE the claim so a lost response can be retried with the same identity
    inv = hardware.simulated_inventory(seed) if simulate else hardware.inventory()
    client = Client(server, None, ca_file=ca_file, insecure=insecure)
    body = {
        "enrollment_token": token,
        "request_id": cfg.data["enroll_request_id"],
        "secret_hash": hashlib.sha256(secret.encode()).hexdigest(),
        "name": name,
        "agent_version": __version__,
        "hardware": inv,
        "simulated": simulate,
        "profile_id": "simulated-host" if simulate else None,
    }
    res = client.post("/api/agent/v1/enroll", body, retries=6)
    cfg.data["device_id"] = res["device_id"]
    cfg.save()
    return res


class Agent:
    def __init__(
        self,
        data_dir: Path,
        *,
        once: bool = False,
        robot_sim: bool | None = None,
        gateway_port: int | None = None,
        sim_faults: dict[str, Any] | None = None,
    ):
        self.cfg = AgentConfig(data_dir)
        if not self.cfg.data.get("device_id") or not self.cfg.secret():
            raise SystemExit("not enrolled: run `convoy-agent enroll` first")
        self.data_dir = Path(data_dir)
        self.simulate = bool(self.cfg.data.get("simulate"))
        self.once = once
        self.device_id = self.cfg.data["device_id"]
        self.server = self.cfg.data["server"]
        self.lock_fd = None
        self.journal = Journal(self.data_dir / "journal.db")
        self.client = Client(
            self.server,
            self.cfg.credential,
            ca_file=self.cfg.data.get("ca_file"),
            insecure=bool(self.cfg.data.get("insecure")),
        )
        seed = int(self.cfg.data.get("seed", 1))
        self.sensors = (
            hardware.SimulatedSensors(str(self.data_dir), seed)
            if self.simulate
            else hardware.Sensors(str(self.data_dir))
        )
        env_faults = json.loads(os.environ.get("CONVOY_SIM_FAULTS", "{}") or "{}")
        self.sup = RuntimeSupervisor(
            self.data_dir / "runtime",
            simulate=self.simulate,
            sensors=self.sensors,
            sim_faults={**env_faults, **(sim_faults or {})},
        )
        self.gw = Gateway(
            self.sup,
            port=gateway_port if gateway_port is not None else int(self.cfg.data.get("gateway_port", 0)),
            on_span=self._on_span,
            deadline_s=float(self.cfg.data.get("request_deadline_s", 30)),
            on_request_done=self._on_request_done,
        )
        self.boot_id = f"sim-{secrets.token_hex(4)}" if self.simulate else hardware.boot_id()
        # accounting is bound to THIS process incarnation, never to the kernel boot id: a process restart
        # on the same boot must still recover the previous process's durable checkpoint
        self.incarnation = secrets.token_hex(6)
        self.inventory = hardware.simulated_inventory(seed) if self.simulate else hardware.inventory()
        from .compat import classify_local

        self.policy = classify_local(self.inventory)
        self.exec = Executor(
            journal=self.journal,
            client=self.client,
            supervisor=self.sup,
            gateway=self.gw,
            sensors=self.sensors,
            data_dir=self.data_dir,
            device_id=self.device_id,
            simulated=self.simulate,
            server_base=self.server,
            profile_policy=self.policy,
            inventory=self.inventory,
            inventory_reader=None if self.simulate else hardware.inventory,
            emit=self.emit,
        )
        self.live_nonce: str | None = None
        self.live_seq = 0
        self.poll_s = 15
        self._chat_poll_allowed = False
        self._next_chat_poll = 0.0
        self.stop = threading.Event()
        self.client.stop = self.stop  # no new control-plane attempt, no backoff sleep, once stopping
        # set by the signal handler WITHOUT touching any lock (plain attribute); the loop turns it into
        # the Events within one wait slice (SHUTDOWN_WAIT_SLICE_S) from thread context
        self._stop_requested = False
        self._stop_deadline: float | None = None
        self.shutdown_budget_s = SHUTDOWN_BUDGET_S
        self.shutdown_worker_s = SHUTDOWN_WORKER_S
        self.shutdown_drain_s = SHUTDOWN_DRAIN_S
        self.shutdown_reserve_s = SHUTDOWN_RESERVE_S
        self.last_shutdown: dict[str, Any] | None = None
        self._aborted_requests = 0  # control-plane requests woken out of blocking I/O by stop requests
        self._ckpt_thread: threading.Thread | None = None
        self.worker: threading.Thread | None = None
        self.robot = None
        self.robot_wanted = (
            robot_sim
            if robot_sim is not None
            else (self.simulate and bool(self.cfg.data.get("robot_sim", True)))
        )
        self._clock = time.monotonic  # injectable for accounting tests
        self.usage_checkpoint_s = float(self.cfg.data.get("usage_checkpoint_s", 10))
        self._usage_lock = threading.RLock()
        self._usage_started = self._clock()
        self._usage_sampled_at = self._usage_started  # last accumulation instant
        self._usage_rt_mark = self._usage_started  # runtime-up coverage accounted up to this instant
        self._usage_cum: dict[str, float] = {k: 0.0 for k in USAGE_CUM_KEYS}  # since agent start
        self._usage_emitted: dict[str, float] = dict(self._usage_cum)  # cumulative totals already emitted
        self._usage_emit_t = self._usage_started
        self._usage_ckpt_requests = 0
        self._usage_ckpt_t = self._usage_started
        # restart recovery is UNRESOLVED until a successful decision (no previous checkpoint / already
        # consumed) or the durable recovery transition; while unresolved no ordinary checkpoint may
        # overwrite whatever the previous incarnation left behind
        self._usage_recovery_pending = True
        self._usage_ckpt_failures = 0  # retry streak: persist failures since the last durable checkpoint
        self._usage_ckpt_failed_since_wall: float | None = None
        # diagnostic evidence: failures not yet carried by an emitted usage record (survives durable
        # checkpoints; consumed only by the atomic emission that reports it, or by restart recovery)
        self._usage_unreported_failures = 0
        self._usage_unreported_since_wall: float | None = None
        self._usage_recovered: dict[str, Any] | None = None
        self._report_ok_t: float | None = None
        self.pending_challenge: str | None = None
        self.health = "unknown"
        self._flush_lock = threading.Lock()
        self._supervise_lock = threading.Lock()
        self._server_committed: dict[str, int] = {}  # what the CURRENT server acknowledged this session
        self._server_context: dict[str, Any] = {}
        self._lanes_probed = False  # every lane probed against the current server context
        self._restart_note: dict[str, Any] | None = None

    # ---- spool ----
    def emit(self, lane: str, kind: str, body: dict[str, Any]) -> int | None:
        return self.journal.append(lane, kind, body)

    def _on_span(self, span: dict[str, Any]) -> None:
        self.emit(
            "telemetry",
            "span",
            {**span, "device_id": self.device_id, "operation_id": (self.exec.current or {}).get("id")},
        )

    def _on_request_done(self) -> None:
        """Fires after the gateway accounted EVERY answered request (served, rejected, refused: counters
        and, for served ones, the slot interval), so the durable checkpoint contains every completed
        request while the journal persists: a crash then loses only the in-flight request, exposed as
        unknown coverage from its start. When the checkpoint cannot be persisted the guarantee lapses
        visibly instead of silently: the failure is counted (`_usage_ckpt_failures`, carried by the
        next durable checkpoint and usage record), logged, and a broken journal stops the agent for
        restart recovery; after a crash the requests answered since the last DURABLE checkpoint fall
        inside the declared unknown interval, never into the known delta."""
        try:
            self._usage_checkpoint()
        except Exception as e:
            log.debug("per-request usage checkpoint not durable: %s", e)  # accounted in _usage_persist_failed

    RESTORE_LOSS_REASON = "pre_restore_history_unrecoverable"
    RESTORE_LOSS_CHUNK = 1_000_000  # the server's MAX_LOSS_SPAN per declared range

    def flush_spool(
        self, max_batches: int = 3, lanes: tuple[str, ...] = LANES, *, probe: bool = False
    ) -> dict[str, bool]:
        """Flush lanes in priority order; returns {lane: fully_flushed}, true only when the lane has no
        pending record and no unreported loss left after the batches sent. Serialised so the worker thread
        (evidence-before-success ordering) and the tick never post the same records concurrently.

        Restore gap (R48 follow-up): the server answers with its own contiguous frontier
        (`next_expected_seq`, or committed_seq+1). When that frontier is BELOW the lane's local durable
        commit frontier, the span in between was acknowledged by an earlier server and deleted here, so
        it is unrecoverable on both sides: it is declared once as a loss range with reason
        `pre_restore_history_unrecoverable`, and the retained records (same bytes, same sequences) are
        resent right after it. Sequences, identity and the local frontier are never rewritten, and the
        local frontier only ever advances to what the server actually acknowledged. `probe=True` posts
        an empty batch when nothing is pending, to learn the current server's frontier."""
        done: dict[str, bool] = {}
        with self._flush_lock:
            for lane in lanes:
                done[lane] = False
                probed = False
                for _ in range(max_batches):
                    records, losses = self.journal.pending(lane, limit=200)
                    if not records and not losses and (not probe or probed):
                        done[lane] = True
                        break
                    probed = True
                    try:
                        res = self.client.post(
                            "/api/agent/v1/spool", self._spool_body(lane, records, losses), retries=1
                        )
                    except ApiError as e:
                        if e.status == 422 and records:
                            if self._isolate_rejected(lane, records, losses, str(e.body)[:120]):
                                continue  # progress was made (disposition or gap declaration); retry
                        log.debug("spool %s deferred: %s", lane, e)
                        break
                    except Transient as e:
                        log.debug("spool %s deferred: %s", lane, e)
                        break
                    if self._apply_spool_response(lane, res, losses) == "gap":
                        continue  # next batch carries the declaration followed by the retained records
                    left, losses_left = self.journal.pending(lane, limit=1)
                    if not left and not losses_left:
                        done[lane] = True  # only when nothing (records or losses) remains pending
                        break
        return done

    @staticmethod
    def _spool_body(lane: str, records: list[dict[str, Any]], losses: list[dict[str, Any]]) -> dict[str, Any]:
        ordered = sorted(losses, key=lambda x: (int(x["from_seq"]), int(x["to_seq"])))[:64]  # server limit
        return {
            "lane": lane,
            "records": records,
            "loss_ranges": [
                {"from_seq": x["from_seq"], "to_seq": x["to_seq"], "reason": x["reason"]} for x in ordered
            ],
        }

    def _apply_spool_response(self, lane: str, res: dict[str, Any], losses: list[dict[str, Any]]) -> str:
        """The ONLY place local records are retired: strictly to the server's returned committed_seq
        (never inferred from an HTTP 200; deferred records stay retained). Returns "gap" when the server's
        frontier is behind our durable frontier and the unrecoverable span was declared, else "ok"."""
        server_committed = int(res.get("committed_seq", 0) or 0)
        self._server_committed[lane] = server_committed
        local_before = int(self.journal.lane_status()[lane]["committed_seq"])
        sent = sorted(losses, key=lambda x: (int(x["from_seq"]), int(x["to_seq"])))[:64]
        self.journal.commit_lane(lane, server_committed, [x["rowid"] for x in sent])
        next_expected = int(res.get("next_expected_seq") or (server_committed + 1))
        if next_expected <= local_before:
            # [next_expected, local_before] was ACKed before the server's restore and is gone;
            # declared in chunks the server accepts (it ignores spans wider than its limit)
            rid = None
            lo = next_expected
            while lo <= local_before:
                hi = min(local_before, lo + self.RESTORE_LOSS_CHUNK - 1)
                rid = self.journal.declare_loss(lane, lo, hi, self.RESTORE_LOSS_REASON) or rid
                lo = hi + 1
            if rid is not None:
                log.warning(
                    "spool %s: server frontier %s is behind the local durable frontier %s "
                    "(server restored to an older snapshot; context=%s); declaring "
                    "%s for [%s, %s] and resending retained records unchanged",
                    lane, server_committed, local_before, self._server_context,
                    self.RESTORE_LOSS_REASON, next_expected, local_before,
                )  # fmt: skip
            return "gap"
        return "ok"

    def _isolate_rejected(
        self, lane: str, records: list[dict[str, Any]], losses: list[dict[str, Any]], why: str
    ) -> bool:
        """A batch the server refuses as a whole (422: non-finite value, nesting, oversized record) is
        bisected until the single offending record is found; that record gets an explicit disposition
        (`drop_record` -> local loss `rejected_by_server:<reason>`, reported on the next batch).
        Every accepted prefix is applied exactly like a normal batch: records are retired ONLY up to the
        server's returned committed_seq (a deferred prefix stays retained), the pending loss declarations
        travel with each prefix, and a restore gap discovered on the way is declared first (returning
        True so the caller resends with the declaration before any isolation continues). Returns True
        when progress was made, False when the server/transport prevented it."""
        lo, hi = 0, len(records)
        while hi - lo > 1:
            mid = (lo + hi) // 2
            prefix = records[lo:mid]
            try:
                res = self.client.post(
                    "/api/agent/v1/spool", self._spool_body(lane, prefix, losses), retries=0
                )
            except ApiError as e:
                if e.status != 422:
                    return False
                hi = mid
                continue
            except Transient:
                return False
            if self._apply_spool_response(lane, res, losses) == "gap":
                return True  # reconcile the restore gap before isolating anything
            if int(res.get("committed_seq", 0) or 0) < int(prefix[-1]["seq"]):
                # the prefix was not (fully) committed: deferred/not contiguous. Nothing is inferred and
                # nothing is dropped; the caller resends after the server's frontier moves.
                return False
            lo = mid  # the whole prefix is committed by the server; the culprit is in the second half
        bad = records[lo]
        if int(self.journal.lane_status()[lane]["committed_seq"]) + 1 != int(bad["seq"]):
            return False  # the culprit is not at the frontier: reconcile the gap before disposing
        log.error(
            "spool %s: record seq %s refused by the server (%s); disposing of it explicitly",
            lane,
            bad["seq"],
            why,
        )
        self.journal.drop_record(lane, int(bad["seq"]), f"rejected_by_server:{why[:40]}")
        return True

    # ---- lifecycle ----
    def acquire_lock(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock_fd = os.open(str(self.data_dir / "agent.lock"), os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            raise SystemExit("another convoy-agent holds the lock for this data dir") from e

    def start_local(self) -> None:
        """Restart recovery, then start the active release runtime if any, then the gateway. Every
        failure here is routed through the journaled local recovery path (R36); nothing raises for an
        operational failure, and the caller's try/finally releases resources for anything else."""
        self.gw.start()
        try:
            self._usage_recovered = self._recover_usage_after_restart()
            if self._usage_recovered is None and not self._usage_recovery_pending:
                self._usage_checkpoint()  # baseline: this process's own accounting starts durable here
        except Exception as e:
            # never overwrite the previous incarnation's checkpoint before its delta is emitted; the
            # recovery is retried before the next checkpoint or record
            log.error("usage restart recovery not durable yet (%s); retried before the next checkpoint", e)
        rec = self.exec.recover_after_restart()
        self._restart_note = rec
        active = self.journal.get("active_release_id")
        if active and self.sup.state() != "running":
            m = self.exec._cached_manifest(active)
            try:
                if not m:
                    raise RuntimeError_("MANIFEST_MISSING", f"no cached manifest for active release {active}")
                model_path, tmpl, rt_dir, binary = self.exec._paths(m["spec"])
                mismatch = self.exec.fresh_platform_check(m["spec"])
                if mismatch:
                    raise RuntimeError_(
                        "PREFLIGHT_COMPAT", f"retained active release refused at boot: {mismatch}"
                    )
                # the retained active release passes the same launch gate as any launch: its pinned
                # bytes re-read and validated, its OWN bound budget, MemAvailable read now (raises
                # OpFailure RECOVERY_METADATA / RECOVERY_MEMORY; nothing is started on refusal)
                plan = self.exec.launch_admission(
                    m["spec"],
                    active,
                    self.exec.effective_budget(m["spec"], None, active),
                    stage="restart",
                    verify_sha=True,  # the full pinned file is re-hashed: a same-size corruption is refused
                )
                ev = self.sup.start(
                    release_id=active,
                    spec=m["spec"],
                    model_path=model_path,
                    template_path=tmpl,
                    binary=binary,
                    lib_dir=(rt_dir / "lib" if rt_dir and (rt_dir / "lib").exists() else rt_dir),
                )
                if self.simulate:
                    ev["binary_sha256"] = self.exec._executable_sha(m["spec"])
                self.journal.set_many({"health": "ok", "runtime_evidence": ev, "launch_admission": plan})
                self.gw.needs_restart = False
                self.gw.set_mode("production")
            except Exception as e:
                log.error("active release failed to start after restart: %s", e)
                self.journal.set_many({"health": "failed"})
                code = e.code if isinstance(e, (RuntimeError_, OpFailure)) else "RUNTIME_START_FAILED"
                details = dict(e.details) if isinstance(e, (RuntimeError_, OpFailure)) else {}
                rec2 = self.exec.recover_local(OpFailure(code, str(e), "restart", details))
                self._restart_note = {"action": "recovered_on_start", "result": rec2}
        if self.robot_wanted:
            from .robot_sim import RobotSim

            self.robot = RobotSim(self.gw.port)
            self.robot.start()

    def observed(self) -> dict[str, Any]:
        op = self.journal.current_operation()
        stage = (
            STAGE_MAP.get(op["stage"], op["stage"].lower())
            if op
            else (
                "active"
                if self.sup.state() == "running"
                else ("degraded" if self.journal.get("health") == "failed" else "idle")
            )
        )
        health = (
            "ok"
            if self.sup.state() == "running" and self.sup.health()
            else (
                "failed"
                if self.journal.get("active_release_id") or self.journal.get("health") == "failed"
                else "unknown"
            )
        )
        self.health = health
        return {
            "active_release_id": self.journal.get("active_release_id"),
            "recovery_release_id": self.journal.get("recovery_release_id"),
            "stage": stage,
            "health": health,
            "generation": int(self.journal.get("generation", 0) or 0),
            "operation_id": op["id"] if op and not op["id"].startswith("local-") else None,
            "failed_generation_latch": self.journal.get("failed_generation_latch"),
            "runtime": self.journal.get("runtime_evidence") or self.sup.evidence or None,
            "chat": {"supported": not self.simulate, "protocol_version": 1},
            "gateway": {
                "mode": self.gw.mode,
                "port": self.gw.port,
                "stats": self.gw.stats,
                "needs_restart": self.gw.needs_restart,
            },
            "lanes": self.journal.lane_status(),
            "restart": getattr(self, "_restart_note", None),
        }

    def report(self, kind: str = "heartbeat", challenge: str | None = None) -> dict[str, Any]:
        seq = self.journal.next_seq()
        sample = self.sensors.sample(self.sup.state())
        body = {
            "seq": seq, "boot_id": self.boot_id, "kind": kind, "source_ts": now_iso(), "agent_version": __version__,
            "observed": self.observed(), "hardware": self.inventory if seq % 20 == 1 else None, "telemetry": sample,
            "time_confidence": "simulated" if self.simulate else ("server_offset" if self.client.server_offset_s is not None else "unknown"),
            "live_nonce": self.live_nonce, "challenge": challenge,
        }  # fmt: skip
        self.emit(
            "telemetry",
            "telemetry",
            {
                "ts": time.time(),
                **{
                    k: sample.get(k)
                    for k in (
                        "mem_total_mb",
                        "mem_available_mb",
                        "cpu_pct",
                        "gpu_pct",
                        "power_w",
                        "temp_max_c",
                        "disk_free_mb",
                        "runtime_state",
                        "clock_confidence",
                    )
                },
            },
        )
        res = self.client.post("/api/agent/v1/report", body, retries=2)
        self._mark_connected()
        if res.get("live_nonce"):
            self.live_nonce = res["live_nonce"]
        if res.get("applied"):
            self.live_seq = seq
        self.poll_s = int(res.get("poll_interval_s") or self.poll_s)
        gen = int(res.get("generation") or 0)
        if gen > int(self.journal.get("generation", 0) or 0):
            self.journal.set("generation", gen)
        return res

    # ---- usage accounting (durable, offline, crash-honest) ----
    def _mark_connected(self) -> None:
        """Contact bookkeeping (a freshness estimate, not measured online time): the interval between two
        consecutive successful reports counts as connected only when it is within the polling cadence
        (a contiguous contact); after a longer gap nothing is credited and a reconnect is counted."""
        now = self._clock()
        with self._usage_lock:
            self._usage_cum["contacts"] += 1.0
            if self._report_ok_t is not None:
                gap = now - self._report_ok_t
                if gap <= 2.0 * self.poll_s + 5.0:
                    self._usage_cum["connected_s"] += gap
                else:
                    self._usage_cum["reconnects"] += 1.0
            self._report_ok_t = now

    def _usage_sample(self) -> dict[str, float]:
        """Accumulate every population up to now from its own evidence: gateway counters and the
        single-slot inference interval (active), the supervisor's start/stop instants (runtime up),
        process uptime (agent up). Nothing is inferred from resident-model status."""
        now = self._clock()
        with self._usage_lock:
            st = self.gw.stats
            self._usage_cum["requests"] = float(st["requests"])
            self._usage_cum["tokens_in"] = float(st["served_tokens_in"])
            self._usage_cum["tokens_out"] = float(st["served_tokens_out"])
            self._usage_cum["inference_s"] = float(st["inference_s"])
            self._usage_cum["agent_up_s"] = now - self._usage_started
            # every start/stop transition since the last sample, not just the latest instants
            self._usage_cum["runtime_up_s"] += self.sup.up_time(self._usage_rt_mark, now)
            self._usage_rt_mark = now
            self._usage_sampled_at = now
            return dict(self._usage_cum)

    def _usage_checkpoint_dict(
        self, cum: dict[str, float], now: float, emitted: dict[str, float]
    ) -> dict[str, Any]:
        """Checkpoint content for the sampled `cum`; built and written under `_usage_lock` so a snapshot
        can never be overtaken and overwritten by a staler one from another thread."""
        return {
            "schema": USAGE_CKPT_SCHEMA,
            "incarnation": self.incarnation,
            "boot_id": self.boot_id,
            "wall": time.time(),
            "monotonic": now,
            "cum": cum,
            "emitted": dict(emitted),
            "requests": int(cum["requests"]),
            # a request in flight at checkpoint time: its interval is not accounted yet, so a crash
            # now loses coverage from ITS start, not from this checkpoint
            "open_interval_from_wall": self.gw.slot_busy_since_wall,
            # persist failures since the previous durable checkpoint: completed requests in that streak
            # were NOT guaranteed durable (honest, visible lapse of the per-request guarantee)
            "checkpoint_failures": self._usage_ckpt_failures,
            "undurable_since_wall": self._usage_ckpt_failed_since_wall,
            # not yet reported in any usage record: kept durably until the emission that carries it
            "unreported_checkpoint_failures": self._usage_unreported_failures,
            "unreported_since_wall": self._usage_unreported_since_wall,
        }

    def _usage_persist_failed(self, what: str, e: Exception) -> None:
        with self._usage_lock:
            self._usage_ckpt_failures += 1
            self._usage_unreported_failures += 1
            first = self._usage_ckpt_failed_since_wall is None
            if first:
                self._usage_ckpt_failed_since_wall = time.time()
            if self._usage_unreported_since_wall is None:
                self._usage_unreported_since_wall = self._usage_ckpt_failed_since_wall
        (log.error if first else log.debug)(
            "usage %s not durable (%s): completed requests since the last durable checkpoint are not "
            "guaranteed durable until a checkpoint succeeds (failures in streak: %s)",
            what,
            e,
            self._usage_ckpt_failures,
        )
        if self.journal.broken:
            log.critical("journal broken (%s); stopping the agent for restart recovery", self.journal.broken)
            self.request_stop()

    def _usage_after_durable(self, ckpt: dict[str, Any], *, reported: bool = False) -> None:
        """A durable checkpoint ends the retry streak only; the unreported failure evidence is consumed
        solely by the emission (or recovery record) that carried it (`reported=True`)."""
        self._usage_ckpt_requests = ckpt["requests"]
        self._usage_ckpt_t = ckpt["monotonic"]
        self._usage_ckpt_failures = 0
        self._usage_ckpt_failed_since_wall = None
        if reported:
            self._usage_unreported_failures = 0
            self._usage_unreported_since_wall = None

    def _usage_checkpoint(self) -> dict[str, Any]:
        """Durable checkpoint of the cumulative counters and of what was already emitted, so a crash
        loses at most the interval since the last checkpoint (bounded by cadence and request count),
        and that interval is later exposed as UNKNOWN coverage rather than as zeros. Sampling, the
        checkpoint decision and the write happen under one lock: concurrent callers (per-request
        callback, checkpointer, minute record) serialise, so the durable checkpoint never regresses."""
        with self._usage_lock:
            if self._usage_recovery_pending:
                rec = self._recover_usage_after_restart()  # raises while not durable; writes the baseline
                self._usage_recovered = self._usage_recovered or rec
                return self.journal.get("usage_checkpoint")
            cum = self._usage_sample()
            ckpt = self._usage_checkpoint_dict(cum, self._clock(), self._usage_emitted)
            try:
                self.journal.set("usage_checkpoint", ckpt)
            except Exception as e:
                self._usage_persist_failed("checkpoint", e)
                raise
            self._usage_after_durable(ckpt)
            return ckpt

    def _record_usage(self, force: bool = False, journal_wait_s: float | None = None) -> None:
        """Emit one usage record per minute (or on demand) with the measured deltas since the last
        emission; populations are named distinctly and never conflated. Runs independently of the
        report path, so records accumulate durably while offline and drain exactly once by sequence.
        `journal_wait_s` bounds the journal lock acquisition (shutdown): StorageBusy then means the
        record was skipped with nothing written, not a persistence failure."""
        with self._usage_lock:
            now = self._clock()
            if now - self._usage_emit_t < 60 and not force:
                return
            if self._usage_recovery_pending:
                self._usage_recovered = self._usage_recovered or self._recover_usage_after_restart()
            cum = self._usage_sample()
            d = {k: cum[k] - self._usage_emitted.get(k, 0.0) for k in USAGE_CUM_KEYS}
            interval = now - self._usage_emit_t
            ckpt = self._usage_checkpoint_dict(cum, now, cum)
            # the emission below carries the unreported failure evidence, so the checkpoint written in the
            # same transaction already shows it consumed (a crash right after never reports it twice)
            ckpt["unreported_checkpoint_failures"] = 0
            ckpt["unreported_since_wall"] = None
            extra = (
                {
                    "checkpoint_failures": self._usage_unreported_failures,
                    "checkpoint_failures_since_wall": self._usage_unreported_since_wall,
                }
                if self._usage_unreported_failures
                else {}
            )
            # the record and the consumed watermark land in ONE journal transaction: a crash between the
            # two can never produce a second, duplicate emission on recovery
            try:
                self.journal.append_and_set(
                    "usage",
                    "usage",
                    self._usage_record(d, interval, **extra),
                    "usage_checkpoint",
                    ckpt,
                    lock_timeout_s=journal_wait_s,
                )
            except StorageBusy:
                raise  # bounded skip: the previous durable checkpoint stands, nothing was attempted
            except Exception as e:
                self._usage_persist_failed("record", e)
                raise
            self._usage_emitted = dict(cum)
            self._usage_emit_t = now
            self._usage_after_durable(ckpt, reported=True)

    @staticmethod
    def _usage_record(d: dict[str, float], interval_s: float, **extra: Any) -> dict[str, Any]:
        mins = lambda k: round(max(0.0, d.get(k, 0.0)) / 60.0, 4)  # noqa: E731
        return {
            "schema": USAGE_SCHEMA,
            "ts": time.time(),
            "interval_s": round(interval_s, 3),
            "inference_requests": int(d.get("requests", 0)),
            "tokens_in": int(d.get("tokens_in", 0)),
            "tokens_out": int(d.get("tokens_out", 0)),
            "inference_minutes": mins("inference_s"),  # time the inference slot was busy
            "runtime_up_minutes": mins("runtime_up_s"),  # supervisor child running
            "agent_up_minutes": mins("agent_up_s"),  # process uptime
            "contact_minutes": mins("connected_s"),  # contact time counted only within the report cadence
            "contacts": int(d.get("contacts", 0)),  # successful report round trips
            "reconnects": int(d.get("reconnects", 0)),  # contacts after a gap longer than the cadence
            "unknown_coverage_s": 0.0,
            **extra,
        }

    def _recover_usage_after_restart(self) -> dict[str, Any] | None:
        """On startup: the previous process's last durable checkpoint yields the KNOWN, not yet emitted
        delta (emitted as measured) and an explicit UNKNOWN coverage interval from that checkpoint to
        this restart (which includes whatever happened up to the crash and any downtime; it is never
        reported as zeros or as measured activity). The recovery record and THIS process's baseline
        checkpoint land in one journal transaction: there is no state in which the record exists
        without the previous checkpoint being consumed, or vice versa. While that transaction cannot be
        made durable the previous checkpoint is left untouched and the recovery is retried before any
        later checkpoint or record (`_usage_recovery_pending`)."""
        with self._usage_lock:
            ckpt = self.journal.get("usage_checkpoint")
            if not ckpt or ckpt.get("incarnation") == self.incarnation:
                self._usage_recovery_pending = False
                return None
            cum, emitted = ckpt.get("cum") or {}, ckpt.get("emitted") or {}
            d = {k: float(cum.get(k, 0.0)) - float(emitted.get(k, 0.0)) for k in USAGE_CUM_KEYS}
            now_wall = time.time()
            from_ts = float(ckpt.get("wall") or now_wall)
            open_from = ckpt.get("open_interval_from_wall")
            if open_from is not None:
                from_ts = min(from_ts, float(open_from))  # the in-flight request's time was never accounted
            unknown = max(0.0, now_wall - from_ts)
            # a checkpoint without `schema` AND without `incarnation` predates the rework: its durations
            # were sampled with the old semantics and must not become measured populations
            legacy = ckpt.get("schema") != USAGE_CKPT_SCHEMA and not ckpt.get("incarnation")
            rec = (
                legacy_checkpoint_recovery_record(ckpt, now_wall)
                if legacy
                else self._usage_record(
                    d,
                    0.0,
                    recovered_after_restart=True,
                    previous_boot_id=ckpt.get("boot_id"),
                    previous_incarnation=ckpt.get("incarnation"),
                    previous_checkpoint_failures=int(ckpt.get("unreported_checkpoint_failures") or 0),
                    previous_undurable_since_wall=ckpt.get("unreported_since_wall"),
                    unknown_coverage_s=round(unknown, 3),
                    unknown_interval={
                        "from_ts": from_ts,
                        "to_ts": now_wall,
                        "reason": "restart_before_durable_checkpoint",
                    },
                )
            )
            baseline = self._usage_checkpoint_dict(self._usage_sample(), self._clock(), self._usage_emitted)
            try:
                if any(abs(v) > 1e-9 for v in d.values()) or unknown > 0:
                    self.journal.append_and_set("usage", "usage", rec, "usage_checkpoint", baseline)
                else:
                    self.journal.set("usage_checkpoint", baseline)
            except Exception as e:
                self._usage_recovery_pending = True
                self._usage_persist_failed("restart recovery", e)
                raise
            self._usage_recovery_pending = False
            self._usage_after_durable(baseline)
        log.warning(
            "usage: recovered a known delta from the previous process (%s requests) and declared "
            "%.1f s of unknown coverage since its last durable checkpoint",
            rec["inference_requests"],
            unknown,
        )
        return rec

    def _final_usage_record(self, timeout_s: float) -> bool:
        """Shutdown's partial-minute record under ONE deadline: the accounting lock and then the
        journal lock (the record's transaction) are each taken with what remains of it. A writer holding
        either lock past the deadline (checkpointer, gateway callback, a spool/operation writer inside a
        journal transaction: separate locks, so a free accounting lock proves nothing about the journal)
        means the record is skipped and reported, never waited for without bound and never written
        partially. The missed minute is then covered by the next start's recovery record as unknown
        coverage from the last durable checkpoint, not invented; a still-pending restart recovery is left
        to the next start for the same reason."""
        deadline = time.monotonic() + max(0.0, timeout_s)
        if not self._usage_lock.acquire(timeout=max(0.0, timeout_s)):
            log.error("final usage record skipped: accounting lock busy for %.1f s", timeout_s)
            return False
        try:
            if self._usage_recovery_pending:
                log.error(
                    "final usage record skipped: restart recovery still pending (left to the next start)"
                )
                return False
            left = max(0.0, deadline - time.monotonic())
            self._record_usage(force=True, journal_wait_s=left)
            return True
        except StorageBusy as e:
            log.error(
                "final usage record skipped: %s (a writer holds a journal transaction); the last durable "
                "checkpoint stands and the next start declares the gap as unknown coverage",
                e,
            )
            return False
        except Exception:
            log.exception("final usage record failed")
            return False
        finally:
            self._usage_lock.release()

    def _usage_checkpointer(self) -> None:
        while not self.stop.wait(self.usage_checkpoint_s):
            try:
                self._record_usage()
                if self._clock() - self._usage_ckpt_t >= self.usage_checkpoint_s:
                    self._usage_checkpoint()
            except Exception:
                log.exception("usage checkpoint failed")

    def _progress(self, op: dict[str, Any], progress: dict[str, Any]) -> None:
        try:
            self.client.post(
                f"/api/agent/v1/operations/{op['id']}/outcome",
                {"status": "running", "progress": progress},
                retries=1,
            )
        except (ApiError, Transient):
            pass

    def _evidence_published(self, op_id: str, outcome: dict[str, Any]) -> bool | str:
        """R15 follow-up ordering: a `succeeded` deploy/eval outcome cites an eval result that the
        CURRENT server must already hold. A historical local ACK is not proof (the server may have been
        restored to an older snapshot), so the proof is the current server's acknowledged critical
        frontier learned in this session (flushing/probing the lane first). Returns True (proven),
        False (defer; the outcome stays durable) or "lost" when the record is covered by a declared
        pre-restore loss: it was acknowledged by an earlier server and deleted here, so the operation
        can no longer be proven and must be reported as EVIDENCE_LOST_ON_RESTORE."""
        if outcome.get("status") != "succeeded":
            return True
        row = self.journal.operation(op_id) or {}
        seq = (row.get("detail") or {}).get("eval_spool_seq")
        if row.get("type") not in ("deploy", "eval") or seq is None:
            return True
        seq = int(seq)
        if self.journal.loss_covers("critical", seq):
            return "lost"
        if self._server_committed.get("critical", -1) >= seq:
            return True
        self.flush_spool(max_batches=10, lanes=("critical",), probe=True)
        if self.journal.loss_covers("critical", seq):
            return "lost"
        return self._server_committed.get("critical", -1) >= seq

    def _evidence_lost_outcome(self, op_id: str, outcome: dict[str, Any]) -> dict[str, Any]:
        """Replace a locally-succeeded outcome whose qualification evidence is unrecoverable on the
        restored server with a structured failure; the local runtime state is reported truthfully."""
        row = self.journal.operation(op_id) or {}
        seq = (row.get("detail") or {}).get("eval_spool_seq")
        failure = {
            "code": "EVIDENCE_LOST_ON_RESTORE",
            "stage": "report",
            "message": "the eval result cited by this outcome was acknowledged by an earlier server and "
            "deleted locally; the restored server cannot accept the success without it",
            "details": {
                "eval_result_id": (row.get("detail") or {}).get("eval_result_id"),
                "critical_seq": seq,
                "loss_reason": self.journal.loss_covers("critical", int(seq)) if seq is not None else None,
                "server_context": self._server_context,
                "local_outcome": outcome.get("status"),
            },
        }
        lost = {
            "status": "failed",
            "grant_id": outcome.get("grant_id"),
            "grant_consumed_seq": outcome.get("grant_consumed_seq"),
            "failure": failure,
            "result": {"active_release_id": self.journal.get("active_release_id"), "local_outcome": outcome},
        }
        self.journal.finish_operation(op_id, row.get("stage") or "Succeeded", lost)
        self.emit("critical", "failure", {"ts": time.time(), "operation_id": op_id, **failure})
        return lost

    def _post_outcome(self, op_id: str, outcome: dict[str, Any]) -> bool:
        """Replay the persisted outcome UNCHANGED until the server acknowledges it (R33)."""
        try:
            proven = self._evidence_published(op_id, outcome)
            if proven == "lost":
                log.error("outcome for %s: eval evidence unrecoverable after server restore", op_id)
                outcome = self._evidence_lost_outcome(op_id, outcome)
            elif not proven:
                log.info("outcome for %s deferred until its eval evidence is accepted", op_id)
                return False
            self.client.post(f"/api/agent/v1/operations/{op_id}/outcome", outcome, retries=2)
            self.journal.mark_acked(op_id)
            return True
        except ApiError as e:
            log.error("outcome for %s rejected by server (%s): %s", op_id, e.status, str(e.body)[:200])
            self.emit(
                "critical",
                "failure",
                {
                    "ts": time.time(),
                    "operation_id": op_id,
                    "code": "OUTCOME_REJECTED",
                    "stage": "report",
                    "message": f"server rejected outcome: HTTP {e.status}",
                    "details": {"body": str(e.body)[:500]},
                },
            )
            if e.status in (404, 409, 422):
                self.journal.mark_acked(op_id)  # nothing further can be done; keep evidence in spool
            return False
        except Transient:
            return False

    def _run_operation(self, op: dict[str, Any]) -> None:
        """Worker entry. Outer boundary (R35): whatever escapes the executor, the journal row ends
        terminal, the gateway is not left closed over a healthy runtime, and the outcome is posted."""
        outcome: dict[str, Any] | None = None
        try:
            outcome = self.exec.execute(
                op,
                live_seq_getter=lambda: self.live_seq,
                boot_id=self.boot_id,
                report_progress=self._progress,
            )
        except Exception as e:
            log.exception("operation %s escaped the executor boundary", op["id"])
            outcome = self._settle_escaped(op, e)
        try:
            if outcome is not None and outcome.get("durable") is False:
                # the terminal row could not be persisted: never post or ACK an invented terminal; the
                # executor already closed admission and stopped the runtime. Stop the agent so the
                # service restarts and settles from the durable journal (restart recovery).
                log.error(
                    "operation %s: terminal state not durable; stopping the agent for restart recovery",
                    op["id"],
                )
                self.request_stop()
            elif outcome is not None and outcome.get("status") != "deferred":
                self._post_outcome(op["id"], outcome)
        except Exception:
            log.exception("posting the outcome for %s failed; it stays journaled for replay", op["id"])
        self._supervise_runtime()

    def _settle_escaped(self, op: dict[str, Any], e: Exception) -> dict[str, Any] | None:
        outcome = {
            "status": "failed",
            "failure": {
                "code": "STORAGE_ERROR" if isinstance(e, StorageError) else "UNEXPECTED_ERROR",
                "stage": "worker",
                "message": f"{type(e).__name__}: {str(e)[:500]}",
                "details": {},
            },
            "result": {"active_release_id": self.journal.get("active_release_id")},
        }
        try:
            row = self.journal.operation(op["id"])
            if row and not row["terminal"]:
                if row["stage"] in ("Cutover", "Evaluating", "Probation", "Recovering"):
                    # disruptive stage: bounded recovery to the retained release or Degraded
                    from .executor import OpFailure as _OF

                    f = _OF(outcome["failure"]["code"], outcome["failure"]["message"], row["stage"].lower())
                    return self.exec._rollback(row, f, row.get("grant"), row.get("grant_consumed_seq"))
                self.journal.finish_operation(op["id"], "Failed", outcome)
            elif row and row.get("outcome"):
                return row["outcome"]
        except Exception:
            log.exception("journal unavailable while settling %s", op["id"])
        if self.sup.state() == "running" and self.sup.health() and self.gw.mode != "production":
            self.gw.set_mode("production")
        elif self.sup.state() != "running":
            self.gw.set_mode("closed")
        return outcome

    def _supervise_runtime(self) -> None:
        """Idle supervision (R37): a gateway request whose runtime could not be proven idle latches
        `needs_restart`; production stays closed until an owned-child replacement is verified. Runs on
        every tick (and after every operation) when no operation is in flight."""
        if (
            self.worker is not None
            and self.worker.is_alive()
            and threading.current_thread() is not self.worker
        ):
            return
        with self._supervise_lock:
            if self.gw.needs_restart or (
                self.sup.state() == "crashed" and self.journal.get("active_release_id")
            ):
                self._restart_runtime_controlled()

    def _restart_runtime_controlled(self) -> None:
        active = self.journal.get("active_release_id")
        m = self.exec._cached_manifest(active) if active else None
        self.gw.set_mode("closed")
        if not m:
            log.error("controlled restart impossible: no active release manifest; production stays closed")
            self.sup.stop()
            self.journal.set("health", "failed")
            return
        gen_before = self.sup.generation
        mismatch = self.exec.fresh_platform_check(m["spec"])  # before the old child is stopped
        if mismatch:
            log.error("controlled restart refused: %s; production stays closed", mismatch)
            self.sup.stop()
            self.journal.set("health", "failed")
            return
        stop = self.sup.stop()
        model_path, tmpl, rt_dir, binary = self.exec._paths(m["spec"])
        try:
            if not stop.get("stopped"):
                raise RuntimeError_("RUNTIME_STOP_FAILED", "previous runtime did not exit", stop)
            # launch gate with the old child really gone: pinned bytes re-read and validated, the
            # release's OWN bound budget, MemAvailable read now (raises OpFailure; no child started)
            plan = self.exec.launch_admission(
                m["spec"],
                active,
                self.exec.effective_budget(m["spec"], None, active),
                stage="restart",
                verify_sha=True,  # the full pinned file is re-hashed before the replacement child starts
            )
            ev = self.sup.start(
                release_id=active,
                spec=m["spec"],
                model_path=model_path,
                template_path=tmpl,
                binary=binary,
                lib_dir=(rt_dir / "lib" if rt_dir and (rt_dir / "lib").exists() else rt_dir),
            )
            if self.sup.generation <= gen_before or not self.sup.health():
                raise RuntimeError_("HEALTH_FAILED", "replacement child did not verify as fresh and healthy")
            if self.simulate:
                ev["binary_sha256"] = self.exec._executable_sha(m["spec"])
            self.journal.set_many({"health": "ok", "runtime_evidence": ev, "launch_admission": plan})
            self.gw.needs_restart = False  # only after a verified fresh child + health evidence
            self.gw.set_mode("production")
            self.emit(
                "telemetry",
                "log",
                {
                    "ts": time.time(),
                    "level": "warning",
                    "source": "agent",
                    "message": "controlled runtime restart after an unconfirmed-idle request",
                    "attrs": {"release_id": active, "generation": self.sup.generation, "stop": stop},
                },
            )
        except Exception as e:
            log.error("controlled restart failed: %s", e)
            if isinstance(
                e, OpFailure
            ):  # the launch gate refused: keep the refusal as the last launch record
                self.journal.set(
                    "launch_admission",
                    {
                        "refused": True,
                        "code": e.code,
                        "stage": e.stage,
                        "message": str(e),
                        "details": dict(e.details),
                    },
                )
            self.journal.set("health", "failed")
            code = e.code if isinstance(e, (RuntimeError_, OpFailure)) else "RUNTIME_START_FAILED"
            details = dict(e.details) if isinstance(e, (RuntimeError_, OpFailure)) else {}
            rec = self.exec.recover_local(OpFailure(code, str(e), "restart", details))
            self._restart_note = {"action": "recovered_after_restart_failure", "result": rec}

    def tick(self) -> None:
        if self.stop.is_set() or self._stop_requested:
            return  # stopping: no report, no dispatch, no flush; shutdown() owns what happens next
        self._chat_poll_allowed = False
        # 0) idle supervision of the owned child (R37)
        self._supervise_runtime()
        busy = self.worker is not None and self.worker.is_alive()
        running_id = (self.exec.current or {}).get("id") if busy else None
        # 1) deliver unacked terminal outcomes from a previous run, unchanged (R33)
        for op in self.journal.unacked_terminal():
            if op["id"] == running_id:
                continue
            oc = op.get("outcome") or {}
            if oc.get("status") in ("succeeded", "failed"):
                self._post_outcome(op["id"], oc)
            else:
                self.journal.mark_acked(op["id"])  # dropped/local rows: nothing the server is owed
        # 2) usage is recorded durably BEFORE any network step (offline periods still accumulate)
        self._record_usage()
        # 3) report (reconcile when challenged)
        try:
            kind = "reconcile" if self.pending_challenge else "heartbeat"
            res = self.report(kind, challenge=self.pending_challenge)
        except (ApiError, Transient) as e:
            log.warning("report failed: %s (local runtime keeps serving)", e)
            return
        if self.stop.is_set() or self._stop_requested:
            return  # the response arrived after a stop request: nothing new is dispatched or flushed
        self.pending_challenge = res.get("reconcile_challenge")
        ctx = {k: res.get(k) for k in ("quarantine", "dispatch_paused", "restore", "restored_at") if k in res}
        if ctx != self._server_context or not self._lanes_probed:
            # first contact, or the server's restoration context changed (e.g. quarantine lifted after a
            # restore): probe EVERY lane, empty ones included, so a server whose frontier fell behind
            # ours is reconciled now, without waiting for a new record on that lane (restore-gap follow-up)
            self._server_context = ctx
            probed = self.flush_spool(lanes=LANES, probe=True)
            self._lanes_probed = all(probed.values())
        if res.get("quarantine"):
            log.warning(
                "server is in restore quarantine; no operations will be accepted until quarantine is lifted"
            )
        # 4) run at most one delivered operation; a resumed pre-grant row is admitted again (R40)
        busy = self.worker is not None and self.worker.is_alive()
        wire_ops = res.get("operations") or []
        if not busy and not self.stop.is_set() and not self._stop_requested:
            for w in wire_ops:
                if w.get("cancel_requested") and w["status"] in ("delivered", "pending"):
                    continue
                if w["status"] not in ("delivered", "pending", "granted", "running"):
                    continue
                row = self.journal.operation(w["id"])
                if row is not None and (row["terminal"] or row["stage"] not in Executor.PRE_GRANT_STAGES):
                    continue
                self.worker = threading.Thread(
                    target=self._run_operation, args=(w,), name=f"op-{w['id']}", daemon=True
                )
                self.worker.start()
                break
            else:
                self._drop_unwanted_pre_grant_row(res, wire_ops)
        # Only a successful report can enable interactive claims. The server rechecks current
        # operation, restore and credential admission on every claim. One main thread arbitrates
        # this worker with operations; the heartbeat cadence remains independent of Chat.
        self._chat_poll_allowed = bool(
            not wire_ops and not res.get("dispatch_paused") and not res.get("quarantine")
        )
        self._maybe_start_chat()
        # 5) evidence
        self.flush_spool()

    def _maybe_start_chat(self) -> None:
        """Claim at most once a second through the same operation worker, without extra heartbeats."""
        now = time.monotonic()
        if now < self._next_chat_poll:
            return
        self._next_chat_poll = now + CHAT_POLL_INTERVAL_S
        if (
            not self._chat_poll_allowed
            or self.simulate
            or self.gw.mode != "production"
            or (self.worker and self.worker.is_alive())
            or self.stop.is_set()
            or self._stop_requested
        ):
            return
        from .chat import run as run_chat

        self.worker = threading.Thread(target=run_chat, args=(self,), name="chat-relay", daemon=True)
        self.worker.start()

    def _drop_unwanted_pre_grant_row(self, res: dict[str, Any], wire_ops: list[dict[str, Any]]) -> None:
        """A resumed pre-grant row the server no longer delivers (and dispatch is not paused, so the
        list is authoritative) is closed locally; the server does not own it any more."""
        cur = self.journal.current_operation()
        if cur is None or cur["stage"] not in Executor.PRE_GRANT_STAGES or cur["id"].startswith("local-"):
            return
        if res.get("dispatch_paused") or res.get("quarantine"):
            return
        if cur["id"] in {w["id"] for w in wire_ops}:
            return
        self.journal.finish_operation(
            cur["id"],
            "Dropped",
            {"status": "dropped", "reason": "not redelivered by the server after restart"},
            acked=True,
        )

    def install_signal_handlers(self) -> bool:
        """R72: under systemd (KillMode=mixed) SIGTERM reaches only the agent process. Turn it (and
        SIGINT) into a controlled stop: the in-flight operation is cancelled, the loop exits, and
        `shutdown()` closes the gateway, stops the owned child, flushes the spool best-effort and
        releases the lock — well inside the unit's TimeoutStopSec. Only the main thread may install
        handlers; embedded runs (tests, sim-fleet threads) simply skip this."""
        if threading.current_thread() is not threading.main_thread():
            return False

        def handler(signum, frame):
            log.info("signal %s received: stopping the agent cleanly", signum)
            self._signal_stop()

        try:
            signal.signal(signal.SIGTERM, handler)
            signal.signal(signal.SIGINT, handler)
        except (ValueError, OSError):
            return False
        return True

    def _signal_stop(self) -> None:
        """The signal-handler half of a stop. A Python signal handler runs on the main thread between
        two bytecodes, possibly INSIDE a critical section of that same thread, so it must take no
        lock: it only assigns plain attributes (the stop flag and the budget deadline), closes gateway
        admission (attribute writes) and shuts down the sockets of in-flight control-plane requests
        (the registry is lock-free). Event.set() and everything else happen in `request_stop()`, which
        the loop calls from thread context within one wait slice; the client's own plain flag makes
        it stop retrying immediately."""
        if not self._stop_requested:
            self._stop_requested = True
            self._stop_deadline = time.monotonic() + self.shutdown_budget_s
        self.gw.set_mode("closed")
        aborted = self.client.abort_inflight()
        if aborted:
            self._aborted_requests += aborted

    def request_stop(self) -> None:
        """Ask the loop to end (thread context: the run loop after a signal, a journal failure, embedded
        runs). Starts the single shutdown budget, cancels the running operation at its next checkpoint,
        closes gateway admission at once and wakes any control-plane request blocked in I/O so the
        caller thread returns instead of waiting out its timeout. Touches no journal state."""
        first = not self._stop_requested
        self._stop_requested = True
        if first or self._stop_deadline is None:
            self._stop_deadline = time.monotonic() + self.shutdown_budget_s
        self.exec.cancel.set()
        self.stop.set()
        self.gw.set_mode("closed")
        aborted = self.client.abort_inflight()
        if aborted:
            self._aborted_requests += aborted
            log.info("stop requested: %d in-flight control-plane request(s) aborted", aborted)

    def _wait_or_stop(self, seconds: float) -> None:
        """The loop's poll wait in short slices: a stop set by the signal handler (plain flag, no
        Event) ends it within SHUTDOWN_WAIT_SLICE_S and is promoted to the Events here."""
        end = time.monotonic() + seconds
        while not self.stop.is_set():
            if self._stop_requested:
                self.request_stop()
                return
            left = end - time.monotonic()
            if left <= 0:
                return
            self.stop.wait(min(SHUTDOWN_WAIT_SLICE_S, left))
            if time.monotonic() < end:
                self._maybe_start_chat()

    def run(self) -> dict[str, Any]:
        self.acquire_lock()
        # only the exclusive owner of the data dir may inspect/signal a recorded orphan child (R39)
        self._orphan_note = self.sup.reap_orphan()
        self.install_signal_handlers()
        try:
            self.start_local()
            self._ckpt_thread = threading.Thread(
                target=self._usage_checkpointer, name="usage-checkpoint", daemon=True
            )
            self._ckpt_thread.start()
            log.info(
                "agent %s up: device=%s simulate=%s gateway=127.0.0.1:%s",
                __version__,
                self.device_id,
                self.simulate,
                self.gw.port,
            )
            while not self.stop.is_set() and not self._stop_requested:
                try:
                    self.tick()
                except Exception:
                    log.exception("tick failed")
                if self.once and not (self.worker and self.worker.is_alive()):
                    break
                self._wait_or_stop(self.poll_s if not self.simulate else min(self.poll_s, 2))
        finally:
            self.last_shutdown = self.shutdown()
        return self.last_shutdown

    def shutdown(self) -> dict[str, Any]:
        """Bounded, ordered shutdown under ONE monotonic budget (SHUTDOWN_BUDGET_S from the stop request):

        1. gateway admission closed, the operation thread cancelled and joined within its share
           (its control-plane I/O is stop-aware; a pre-grant operation defers and resumes at restart);
        2. no new gateway connections; the owned child stopped and its exit verified (the supervisor lock
           serialises this with any start still in progress);
        3. handlers still running finish (the child is gone, so they fail fast) and their per-request
           checkpoints land while the journal is open; the checkpointer thread joins;
        4. the final partial-minute usage record and the consumed watermark are written in one
           transaction (durable local accounting, no invented time);
        5. nothing is uploaded: queued spool bytes and ACK frontiers stay durable for the next start's
           ordinary authenticated replay;
        6. the journal is closed and the data-directory lock released ONLY when nothing can write or
           start a child any more. Otherwise ownership is left to process exit (the OS releases the lock,
           SQLite's journal keeps the file consistent) and the summary says which part did not finish.

        Returns the summary with measured durations; `complete` is False when a bound was not met."""
        t_start = time.monotonic()
        self.request_stop()
        deadline = self._stop_deadline or (t_start + self.shutdown_budget_s)

        def left() -> float:
            return max(0.0, deadline - time.monotonic())

        summary: dict[str, Any] = {"complete": True, "limits": [], "aborted_requests": self._aborted_requests}
        # 1. operation thread
        t = time.monotonic()
        worker = self.worker
        if worker is not None and worker.is_alive():
            worker.join(
                timeout=min(
                    self.shutdown_worker_s, max(0.0, left() - self.shutdown_drain_s - self.shutdown_reserve_s)
                )
            )
        worker_done = worker is None or not worker.is_alive()
        summary["worker_s"] = round(time.monotonic() - t, 3)
        if not worker_done:
            summary["limits"].append("operation thread still running after its shutdown share")
        if self.robot:
            self.robot.stop()
        # 2. no new gateway connections, then the owned child under ONE absolute deadline shared by the
        #    supervisor lock wait, the SIGTERM grace, the SIGKILL reap and the reader join
        self.gw.stop()
        t = time.monotonic()
        stop = self.sup.stop(
            timeout_s=20.0,
            deadline=deadline - self.shutdown_drain_s - self.shutdown_reserve_s,
        )
        summary["runtime_stop"] = stop
        summary["runtime_stop_s"] = round(time.monotonic() - t, 3)
        runtime_stopped = bool(stop.get("stopped"))
        if not runtime_stopped:
            summary["limits"].append(f"owned runtime not verified stopped ({stop.get('method')})")
        # 3. handlers still running, then the checkpointer
        t = time.monotonic()
        drained = self.gw.drain(min(self.shutdown_drain_s, left()))
        summary["drain_s"] = round(time.monotonic() - t, 3)
        if not drained:
            summary["limits"].append(
                f"{self.gw.inflight} gateway request(s) still running after the drain share"
            )
        # Runtime stop/drain can unblock a chat worker that exceeded its initial join share.
        # Re-check under the same overall deadline before deciding whether ownership can release.
        if not worker_done and worker is not None:
            worker.join(timeout=min(1.0, left()))
            worker_done = not worker.is_alive()
            if worker_done:
                summary["limits"].remove("operation thread still running after its shutdown share")
        if self._ckpt_thread is not None and self._ckpt_thread.is_alive():
            self._ckpt_thread.join(timeout=min(2.0, left()))
        ckpt_done = self._ckpt_thread is None or not self._ckpt_thread.is_alive()
        if not ckpt_done:
            summary["limits"].append("usage checkpointer still running")
        # 4. final local accounting (record + watermark in one transaction). Bounded: the accounting
        #    lock is taken with a deadline so a writer that did not quiesce cannot hold shutdown here
        writers_quiescent = worker_done and drained and ckpt_done
        summary["final_usage_record"] = self._final_usage_record(min(2.0, left()))
        if not summary["final_usage_record"]:
            summary["limits"].append("final usage record not written")
        # 5. no spool upload during shutdown (durable for replay at the next start); lane status is
        #    read only when no writer can hold the journal lock against us
        if writers_quiescent and not self.journal.closed:
            summary["spool_pending"] = {
                lane: max(0, int(st["next_seq"]) - 1 - int(st["committed_seq"]))
                for lane, st in self.journal.lane_status().items()
            }
        # 6. ownership: released only when nothing can write and the owned child is verified stopped
        quiescent = writers_quiescent and runtime_stopped
        if quiescent:
            self.journal.close()
            if self.lock_fd is not None:
                try:
                    fcntl.flock(self.lock_fd, fcntl.LOCK_UN)
                    os.close(self.lock_fd)
                except OSError:
                    pass
                self.lock_fd = None
            summary["released"] = True
        else:
            summary["released"] = False
            log.error(
                "shutdown incomplete within %.0f s: %s; journal and lock are left to process exit "
                "(restart recovery settles the journal)",
                self.shutdown_budget_s,
                "; ".join(summary["limits"]),
            )
        summary["complete"] = not summary["limits"]
        summary["total_s"] = round(time.monotonic() - t_start, 3)
        summary["budget_s"] = self.shutdown_budget_s
        log.info(
            "shutdown %s in %.3f s: %s",
            "complete" if summary["complete"] else "INCOMPLETE",
            summary["total_s"],
            summary,
        )
        return summary
