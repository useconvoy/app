"""Operation executor: the device-side state machine (docs/ARCHITECTURE.md, Stage 3).

Staging -> WaitingGrant -> Cutover -> Evaluating -> Probation -> Succeeded
  Cutover/Evaluating/Probation -- error or restart --> Recovering -> RolledBack | Degraded
  WaitingGrant -> Cancelled (before grant consumption)

Every transition is committed to the journal before the side effect it authorises. Grant validity is
measured with the monotonic clock from request start. Recovery restores the exact retained package and
sets the failed-generation latch; a Degraded device keeps the gateway closed and reports honestly."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from . import gguf
from .client import ApiError, Client, Transient
from .download import DownloadError, download_verified, extract_archive, sha256_file
from .gateway import Gateway
from .harness import Harness
from .journal import Journal, StorageError
from .runtime import RuntimeError_, RuntimeSupervisor

log = logging.getLogger("convoy.agent.executor")

RECOVERY_ATTEMPTS = 2


class OpFailure(Exception):
    def __init__(self, code: str, message: str, stage: str, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.details = details or {}


class GrantExpiredBeforeDisruption(OpFailure):
    """The final monotonic TTL check (immediately before the first disruptive effect) failed: nothing
    was stopped or closed, so the operation fails honestly without any recovery."""

    def __init__(self, ttl: float, elapsed: float):
        super().__init__(
            "GRANT_EXPIRED",
            "grant TTL elapsed (monotonic) before the cutover started; nothing was changed",
            "cutover",
            {"ttl_s": ttl, "elapsed_s": round(elapsed, 3)},
        )


class Executor:
    def __init__(
        self,
        *,
        journal: Journal,
        client: Client,
        supervisor: RuntimeSupervisor,
        gateway: Gateway,
        sensors,
        data_dir: Path,
        device_id: str,
        simulated: bool,
        server_base: str,
        profile_policy: dict[str, Any] | None = None,
        emit=None,
        inventory: dict[str, Any] | None = None,
        inventory_reader=None,
    ):
        self.j = journal
        self.client = client
        self.sup = supervisor
        self.gw = gateway
        self.sensors = sensors
        self.data_dir = Path(data_dir)
        self.device_id = device_id
        self.simulated = simulated
        self.server_base = server_base
        self.profile_policy = profile_policy or {}
        self.inventory = inventory or {}
        self.inventory_reader = inventory_reader  # fresh re-read immediately before disruption
        self.enforce_platform = not simulated  # the tuple gate applies to physical devices only
        self.emit = emit or (lambda lane, kind, body: None)  # spool writer
        self.cancel = threading.Event()
        self.current: dict[str, Any] | None = None
        self.gateway_stats_at_probation: dict[str, int] | None = None
        (self.data_dir / "cache" / "models").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "cache" / "runtime").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "cache" / "templates").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ helpers
    def _emit_safe(self, lane: str, kind: str, body: dict[str, Any]) -> int | None:
        """Evidence emission never turns a broken journal into a second failure on a failure path."""
        try:
            return self.emit(lane, kind, body)
        except Exception as e:
            log.error("could not spool %s/%s: %s", lane, kind, e)
            return None

    def _log(self, level: str, msg: str, op_id: str | None = None, **attrs) -> None:
        getattr(log, level if level != "warn" else "warning")("%s %s", msg, attrs if attrs else "")
        self._emit_safe(
            "telemetry",
            "log",
            {
                "ts": time.time(),
                "level": level,
                "source": "agent",
                "message": msg,
                "operation_id": op_id,
                "attrs": attrs,
            },
        )

    def _failure(
        self, op_id: str, code: str, stage: str, message: str, details: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        f = {"code": code, "stage": stage, "message": message[:2000], "details": details or {}}
        self._emit_safe(
            "critical",
            "failure",
            {
                "ts": time.time(),
                "operation_id": op_id,
                "release_id": (self.current or {}).get("payload", {}).get("target_release_id"),
                **f,
            },
        )
        return f

    def active_release(self) -> str | None:
        return self.j.get("active_release_id")

    def recovery_release(self) -> str | None:
        return self.j.get("recovery_release_id")

    def _manifest(self, release_id: str) -> dict[str, Any]:
        m = self.client.get(f"/api/agent/v1/releases/{release_id}")
        cached = self.data_dir / "cache" / "manifests"
        cached.mkdir(exist_ok=True)
        (cached / f"{release_id}.json").write_text(json.dumps(m))
        return m

    def _cached_manifest(self, release_id: str) -> dict[str, Any] | None:
        p = self.data_dir / "cache" / "manifests" / f"{release_id}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def _paths(self, spec: dict[str, Any]) -> tuple[Path, Path | None, Path | None, Path | None]:
        f = spec["model"]["file"]
        model = self.data_dir / "cache" / "models" / f"{f['sha256']}.gguf"
        tmpl = None
        if spec.get("template"):
            tmpl = self.data_dir / "cache" / "templates" / f"{spec['template']['sha256']}.jinja"
        art_sha = spec["runtime"].get("artifact_sha256")
        rt_dir = (self.data_dir / "cache" / "runtime" / art_sha) if art_sha else None
        binary = None
        if rt_dir and spec["runtime"].get("artifact_files"):
            for entry in spec["runtime"]["artifact_files"]:
                if str(entry["path"]).endswith(("llama-server", "llama-server.sim")):
                    binary = rt_dir / entry["path"]
        return model, tmpl, rt_dir, binary

    def _executable_sha(self, spec: dict[str, Any]) -> str | None:
        for entry in spec["runtime"].get("artifact_files") or []:
            if str(entry["path"]).endswith(("llama-server", "llama-server.sim")):
                return entry.get("sha256")
        return None

    # ------------------------------------------------------------------ budget policy
    # ONE budget policy on the device, mirroring the server planner (server hardware.effective_budget):
    #   required = weights + KV cache + compute buffer + runtime_overhead + robot_reserve + margin
    #   KV      = 2 * n_layers * n_kv_heads * head_dim * kv_bytes_per_element * ctx_size * parallel
    #   compute = ((n_vocab or 152064) * ubatch + n_embd * ubatch * 16) * 4 bytes
    # The numbers come, per term, from the operation payload's `effective_budget` (the server's frozen
    # policy including per-device overrides), else the budget last bound to the release in the journal,
    # else spec["budget"], else the profile defaults below.
    PROFILE_BUDGET_DEFAULTS = {"runtime_overhead_mb": 700, "robot_reserve_mb": 1536, "margin_mb": 512}
    BUDGET_KEYS = ("runtime_overhead_mb", "robot_reserve_mb", "margin_mb")
    DEFAULT_UBATCH = 128
    DEFAULT_VOCAB = 152064
    KV_BYTES_PER_ELEMENT = 2
    MIB = 1024 * 1024

    @staticmethod
    def _budget_int(v: Any) -> int | None:
        """A non-negative integral budget term; None for anything that is not one (never coerced)."""
        if isinstance(v, bool):
            return None
        if isinstance(v, int) and v >= 0:
            return v
        if isinstance(v, float) and v >= 0 and v == int(v):
            return int(v)
        return None

    def bound_budget(self, release_id: str | None) -> dict[str, Any] | None:
        """The effective budget last bound to a release at admission (journal `effective_budget:<id>`)."""
        if not release_id:
            return None
        b = self.j.get(f"effective_budget:{release_id}")
        return b if isinstance(b, dict) else None

    def effective_budget(
        self,
        spec: dict[str, Any],
        payload_budget: dict[str, Any] | None = None,
        release_id: str | None = None,
    ) -> dict[str, Any]:
        """Resolve the budget terms for ONE release. Precedence per term: `payload_budget` (the deploy /
        recover payload's `effective_budget`, carrying the server's per-device overrides and their
        provenance) -> the budget bound to `release_id` in the journal at its admission (so a retained
        launch without any operation payload keeps the policy it was admitted under) -> spec["budget"]
        -> the profile defaults. Returns the contract shape: the three terms, ubatch_size,
        kv_bytes_per_element, `source` naming each term's origin (device.settings / release.budget /
        profile / operation.payload) and `resolved` naming where this device took it from
        (operation.payload / journal.bound / release.budget / profile)."""
        payload = payload_budget if isinstance(payload_budget, dict) else None
        bound = self.bound_budget(release_id)
        release = spec.get("budget") or {}
        cfg = spec.get("config") or {}
        out: dict[str, Any] = {}
        source: dict[str, str] = {}
        resolved: dict[str, str] = {}
        layers = (
            ("operation.payload", payload, (payload or {}).get("source") or {}),
            ("journal.bound", bound, (bound or {}).get("source") or {}),
            ("release.budget", release, {}),
        )
        for key in self.BUDGET_KEYS:
            for name, src, origins in layers:
                v = self._budget_int((src or {}).get(key))
                if v is not None:
                    out[key] = v
                    origin = origins.get(key)
                    source[key] = origin if isinstance(origin, str) and origin else name
                    resolved[key] = name
                    break
            else:
                out[key] = self.PROFILE_BUDGET_DEFAULTS[key]
                source[key] = resolved[key] = "profile"
        ubatch = None
        for src in (payload, bound):
            ubatch = self._budget_int((src or {}).get("ubatch_size"))
            if ubatch:
                break
        out["ubatch_size"] = ubatch or self._budget_int(cfg.get("ubatch_size")) or self.DEFAULT_UBATCH
        kv_bytes = None
        for src in (payload, bound):
            kv_bytes = self._budget_int((src or {}).get("kv_bytes_per_element"))
            if kv_bytes:
                break
        out["kv_bytes_per_element"] = kv_bytes or self.KV_BYTES_PER_ELEMENT
        out["source"] = source
        out["resolved"] = resolved
        return out

    @classmethod
    def _kv_mb(cls, kvi: dict[str, Any] | None, cfg: dict[str, Any], kv_bytes: int = 2) -> float | None:
        """KV cache size from EXACT positive bounded integer counts; None when any count is missing,
        zero, negative, fractional, boolean or out of bounds (never truncated into a number)."""
        k = kvi or {}
        if any(name in gguf.dimension_errors(k) for name in gguf.REQUIRED_DIMENSIONS):
            return None
        ctx = gguf.strict_int(cfg.get("ctx_size", 2048))
        parallel = gguf.strict_int(cfg.get("parallel", 1)) or 1
        if not ctx or ctx <= 0:
            return None
        return (
            2
            * k["n_layers"]
            * k["n_kv_heads"]
            * k["head_dim"]
            * int(kv_bytes)
            * ctx
            * max(1, parallel)
            / cls.MIB
        )

    @classmethod
    def _compute_buffer_mb(cls, kvi: dict[str, Any] | None, ubatch: int) -> float | None:
        """Logits + activation scratch estimate (server `compute_buffer_mb`); None without n_embd."""
        k = kvi or {}
        n_embd = gguf.strict_int(k.get("n_embd"))
        if not n_embd or n_embd <= 0:
            return None
        vocab = gguf.strict_int(k.get("n_vocab"))
        vocab = vocab if vocab and vocab > 0 else cls.DEFAULT_VOCAB
        return (vocab * ubatch * 4 + n_embd * ubatch * 4 * 16) / cls.MIB

    def required_mb(
        self, spec: dict[str, Any], kvi: dict[str, Any] | None, budget: dict[str, Any]
    ) -> dict[str, Any]:
        """The memory a release needs under `budget` (from `effective_budget`), itemised exactly like
        the server planner. `required_mb` is None when the KV cache OR the compute buffer cannot be
        sized from `kvi` (`unknown_terms` names which); an unknown term is never counted as 0."""
        cfg = spec.get("config") or {}
        weights = float(spec["model"]["total_bytes"]) / self.MIB
        kv = self._kv_mb(kvi, cfg, int(budget["kv_bytes_per_element"]))
        comp = self._compute_buffer_mb(kvi, int(budget["ubatch_size"]))
        overhead = float(budget["runtime_overhead_mb"])
        reserve = float(budget["robot_reserve_mb"])
        margin = float(budget["margin_mb"])
        unknown = [t for t, v in (("kv_cache_mb", kv), ("compute_buffer_mb", comp)) if v is None]
        required = None if unknown else weights + float(kv) + float(comp) + overhead + reserve + margin
        return {
            "unknown_terms": unknown,
            "items": {
                "weights_mb": round(weights, 1),
                "kv_cache_mb": None if kv is None else round(kv, 1),
                "compute_buffer_mb": None if comp is None else round(comp, 1),
                "runtime_overhead_mb": overhead,
                "robot_reserve_mb": reserve,
                "margin_mb": margin,
                "required_mb": None if required is None else round(required, 1),
            },
            "required_mb": None if required is None else round(required, 1),
            "ctx_size": cfg.get("ctx_size", 2048),
            "parallel": cfg.get("parallel", 1),
            "compute_buffer_source": (
                f"estimate, ubatch={budget['ubatch_size']}"
                if comp is not None
                else "unknown: embedding size missing; the requirement is unknown (never counted as 0)"
            ),
        }

    # ------------------------------------------------------------------ compatibility
    MATERIAL_CLAIMS = ("architecture", "tokenizer_model", "file_type", "has_chat_template")

    @staticmethod
    def _template_override_ok(spec: dict[str, Any]) -> bool | None:
        """None when the release pins no template; True when spec["template"] carries text whose
        sha256 equals its pinned hash (a reviewed override); False when it is present but invalid."""
        tmpl = spec.get("template")
        if not tmpl:
            return None
        if not isinstance(tmpl, dict) or not isinstance(tmpl.get("text"), str) or not tmpl.get("text"):
            return False
        return hashlib.sha256(tmpl["text"].encode()).hexdigest() == tmpl.get("sha256")

    def compat_check(self, spec: dict[str, Any], q: dict[str, Any], stage: str) -> dict[str, Any]:
        """Effective compatibility, decided from the PARSED BYTES of the sha256-verified file (`q` is
        gguf.qualification of its header; the manifest's own `status` is never consulted): supported
        architecture, known quantization, supported tokenizer, a chat template (embedded, or a valid
        reviewed release override), positive bounded integer dimensions; and every MATERIAL claim the
        manifest makes (architecture, tokenizer, file type, template presence, counts) must agree with
        the bytes. Raises OpFailure(PREFLIGHT_COMPAT, stage) naming the exact reason; never disrupts."""
        model = spec.get("model") or {}
        want = model.get("gguf") or {}
        got_kvi = q.get("kv_estimate_inputs") or {}
        contradictions: dict[str, Any] = {}
        for key in self.MATERIAL_CLAIMS:
            if want.get(key) is not None and str(want.get(key)) != str(q.get(key)):
                contradictions[key] = {"manifest": want.get(key), "file": q.get(key)}
        want_kvi = want.get("kv_estimate_inputs") or {}
        for k in gguf.DIMENSION_BOUNDS:
            if (
                want_kvi.get(k) is not None
                and got_kvi.get(k) is not None
                and str(want_kvi.get(k)) != str(got_kvi.get(k))
            ):
                contradictions[k] = {"manifest": want_kvi.get(k), "file": got_kvi.get(k)}
        reasons: list[str] = []
        arch = q.get("architecture")
        if arch not in gguf.RECOGNIZED_ARCHS:
            reasons.append(
                f"architecture {arch!r} is not supported (supported: {sorted(gguf.RECOGNIZED_ARCHS)})"
            )
        ft = q.get("file_type")
        if ft is None or str(ft).startswith("unknown"):
            reasons.append(f"quantization (general.file_type) is {ft!r}: not a known file type")
        tok = q.get("tokenizer_model")
        if tok not in gguf.RECOGNIZED_TOKENIZERS:
            reasons.append(
                f"tokenizer {tok!r} is not supported (supported: {sorted(gguf.RECOGNIZED_TOKENIZERS)})"
            )
        override = self._template_override_ok(spec)
        if q.get("has_chat_template"):
            template = "embedded"
        elif override is True:
            template = "release_override"
        else:
            template = None
            reasons.append(
                "no chat template: the file embeds none and the release pins "
                + (
                    "no template override"
                    if override is None
                    else "an override whose text does not match its sha256"
                )
            )
        for name, why in gguf.dimension_errors(got_kvi).items():
            reasons.append(f"dimension {name}: {why}")
        file_view = {k: q.get(k) for k in self.MATERIAL_CLAIMS}
        file_view["kv_estimate_inputs"] = got_kvi
        details = {
            "contradictions": contradictions,
            "reasons": reasons,
            "file": file_view,
            "manifest_provenance": model.get("gguf_provenance"),
        }
        if contradictions:
            raise OpFailure(
                "PREFLIGHT_COMPAT",
                "release manifest GGUF metadata contradicts the hash-verified file ("
                + ", ".join(sorted(contradictions))
                + "); the candidate is not admitted and the incumbent keeps serving",
                stage,
                details,
            )
        if reasons:
            raise OpFailure(
                "PREFLIGHT_COMPAT",
                "GGUF is not compatible with this device's runtime: " + "; ".join(reasons),
                stage,
                details,
            )
        return {
            "architecture": arch,
            "file_type": ft,
            "tokenizer_model": tok,
            "template": template,
            "kv_estimate_inputs": got_kvi,
        }

    # ------------------------------------------------------------------ preflight
    def preflight(self, op: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
        """Cheap ELIGIBILITY before any bytes move: live meminfo readable, conservative disk room for
        staging, platform policy/tuple/backend/artifact. Memory admission here is ADVISORY only: the
        manifest's GGUF counts may be header-derived or operator-supplied and are never trusted for the
        final decision. The only refusal they can cause is a release that cannot fit even into the
        whole pool (weights + advisory KV + compute + overhead + reserve + margin > MemTotal). Unknown
        counts mean 'admission pending byte inspection', never a fabricated budget and never a dead end."""
        spec = manifest["spec"]
        s = self.sensors.sample(self.sup.state())
        total, avail, disk = s.get("mem_total_mb"), s.get("mem_available_mb"), s.get("disk_free_mb")
        if avail is None or total is None:
            raise OpFailure(
                "PREFLIGHT_MEMORY",
                "live MemAvailable/MemTotal not readable; cannot admit",
                "preflight",
                {"sample": s},
            )
        model = spec["model"]
        budget = self.effective_budget(spec, op["payload"].get("effective_budget"), manifest["release_id"])
        advisory_kvi = (model.get("gguf") or {}).get("kv_estimate_inputs") or {}
        req = self.required_mb(spec, advisory_kvi, budget)
        kv_mb = req["items"]["kv_cache_mb"]
        prov = model.get("gguf_provenance") or {}
        items = {
            **req["items"],
            "kv_source": (
                f"manifest (advisory: {prov.get('method', 'unknown')}, byte_verified={prov.get('byte_verified')})"
                if kv_mb is not None
                else "unknown on the manifest: derived from the verified file at staging"
            ),
        }
        plan: dict[str, Any] = {
            "mem_total_mb": total,
            "mem_available_mb": avail,
            "items": items,
            "budget": budget,
            "cutover": "stop_start",
            "admission": "advisory" if kv_mb is not None else "pending_byte_inspection",
        }
        if req["required_mb"] is not None:
            required = float(req["required_mb"])
            plan["required_mb_advisory"] = required
            if required > float(total):
                raise OpFailure(
                    "PREFLIGHT_MEMORY",
                    f"release cannot fit the device at all: advisory requirement {required:.0f} MiB exceeds MemTotal {float(total):.0f} MiB",
                    "preflight",
                    plan,
                )
        need_disk = float(req["items"]["weights_mb"]) + 1024
        if disk is None or float(disk) < need_disk:
            raise OpFailure(
                "PREFLIGHT_DISK",
                f"not enough free disk ({disk} MiB, need {need_disk:.0f} MiB)",
                "preflight",
                {**plan, "disk_free_mb": disk},
            )
        if self.enforce_platform:
            plan["platform"] = self._platform_gate(spec, self.inventory, "preflight")
        if not self.simulated:
            policy = self.profile_policy.get("policy")
            if policy not in ("allowed",):
                raise OpFailure(
                    "PREFLIGHT_COMPAT",
                    f"platform tuple policy is {policy!r}; only 'allowed' tuples may run physical releases",
                    "preflight",
                    {"policy": self.profile_policy},
                )
            if spec["runtime"].get("backend") != "cuda":
                raise OpFailure(
                    "PREFLIGHT_COMPAT", "physical profile requires a CUDA runtime recipe", "preflight"
                )
        if manifest.get("build_status") != "ready" or not spec["runtime"].get("artifact_sha256"):
            raise OpFailure(
                "PREFLIGHT_COMPAT", "release has no concrete runtime artifact (build required)", "preflight"
            )
        return plan

    # ------------------------------------------------------------------ launch admission
    def launch_admission(
        self,
        spec: dict[str, Any],
        release_id: str,
        budget: dict[str, Any],
        *,
        stage: str,
        provisional: bool = False,
        memory_code: str = "RECOVERY_MEMORY",
        metadata_code: str = "RECOVERY_METADATA",
        verify_sha: bool = False,
    ) -> dict[str, Any]:
        """The ONE gate in front of EVERY runtime launch (candidate cutover, rollback recovery, explicit
        recovery operation, boot restart, crash/controlled restart). For the spec about to be launched:
        (a) the pinned, hash-named model file on disk (cache/models/<sha256>.gguf; re-hashed when
        `verify_sha`) parses as a GGUF whose BYTES pass `compat_check` and agree with the manifest;
        (b) that release's own effective `budget` sizes the requirement (`required_mb`);
        (c) MemAvailable read NOW (`sensors.sample`) covers it. `provisional` is the pre-disruption form
        (incumbent still serving: its MEASURED resident set is credited, None credits 0); the final form
        runs after the previous child has really stopped and credits nothing. Raises
        OpFailure(metadata_code | memory_code, stage) and never starts anything; returns the plan."""
        model_path = self._paths(spec)[0]
        f = spec["model"]["file"]
        if not model_path.exists():
            raise OpFailure(
                metadata_code,
                f"pinned model file for {release_id} is missing ({model_path.name}); runtime not started",
                stage,
                {"release_id": release_id, "model_path": str(model_path)},
            )
        sha_verified = False
        if verify_sha:
            if sha256_file(model_path)[0] != f["sha256"]:
                raise OpFailure(
                    metadata_code,
                    f"pinned model file for {release_id} does not match its sha256; runtime not started",
                    stage,
                    {"release_id": release_id, "model_path": str(model_path)},
                )
            sha_verified = True
        try:
            q = gguf.qualification(gguf.read_metadata(str(model_path)))
        except (gguf.GGUFError, OSError) as e:
            raise OpFailure(
                metadata_code,
                f"pinned model file for {release_id} is not a readable GGUF ({e}); runtime not started",
                stage,
                {"release_id": release_id, "model_path": str(model_path)},
            ) from e
        try:
            compat = self.compat_check(spec, q, stage)
        except OpFailure as e:
            raise OpFailure(
                metadata_code,
                f"{release_id}: {e}; runtime not started",
                stage,
                {**e.details, "release_id": release_id},
            ) from e
        kvi = q.get("kv_estimate_inputs") or {}
        req = self.required_mb(spec, kvi, budget)
        if req["required_mb"] is None:
            raise OpFailure(
                metadata_code,
                f"{release_id}: the memory requirement is unknown from the verified GGUF header ({', '.join(req['unknown_terms'])} cannot be sized); the runtime is not started",
                stage,
                {"release_id": release_id, "kv_estimate_inputs": kvi, "unknown_terms": req["unknown_terms"]},
            )
        required = float(req["required_mb"])
        s = self.sensors.sample(self.sup.state())
        avail = s.get("mem_available_mb")
        if avail is None:
            raise OpFailure(
                memory_code,
                f"{release_id}: live MemAvailable not readable; runtime not started",
                stage,
                {"release_id": release_id, "sample": s, "required_mb": required},
            )
        incumbent = None
        if provisional and self.sup.state() == "running":
            incumbent = self.sup.footprint_mb()
        credited = float(incumbent or 0.0) if provisional else 0.0
        headroom = float(avail) + credited - required
        plan: dict[str, Any] = {
            "release_id": release_id,
            "source": "verified_bytes",
            "sha256_verified": sha_verified,
            "compat": compat,
            "kv_estimate_inputs": kvi,
            "budget": budget,
            "items": req["items"],
            "compute_buffer_source": req["compute_buffer_source"],
            "required_mb": required,
            "mem_available_mb": avail,
            "runtime_state": s.get("runtime_state"),
            "provisional": provisional,
            "headroom_mb": round(headroom, 1),
            "sampled_at": time.time(),
        }
        if provisional:
            plan["incumbent_rss_mb"] = None if incumbent is None else round(incumbent, 1)
            plan["incumbent_credit"] = (
                "measured resident set" if incumbent is not None else "unmeasurable: credited 0"
            )
            plan["headroom_mb_provisional"] = round(headroom, 1)
            plan["final_check"] = "MemAvailable re-read after the previous runtime stops, before the launch"
        if headroom < 0:
            terms = ", ".join(f"{k}={v}" for k, v in req["items"].items() if k != "required_mb")
            if provisional:
                msg = (
                    f"{release_id} does not fit the shared-memory budget: MemAvailable {float(avail):.0f} MiB"
                    f" (+{credited:.0f} MiB incumbent credit) < required {required:.0f} MiB [{terms}]"
                    f" (provisional headroom {headroom:.0f} MiB; incumbent still serving)"
                )
            else:
                msg = (
                    f"MemAvailable is {float(avail):.0f} MiB after the previous runtime stopped but"
                    f" {release_id} needs {required:.0f} MiB [{terms}]; runtime not started"
                )
            raise OpFailure(memory_code, msg, stage, plan)
        return plan

    def admission(
        self, op: dict[str, Any], manifest: dict[str, Any], staged: dict[str, Any]
    ) -> dict[str, Any]:
        """BYTE-AUTHORITATIVE admission of a staged candidate, before the grant is requested: the
        compatibility rules and the KV cache come from the sha256-verified file's own header (never the
        manifest). The incumbent keeps serving: what it would release is credited only as its MEASURED
        resident set (None when unmeasurable, credited as 0). The decision is provisional; the final
        gate (`launch_admission` in cutover) re-reads MemAvailable after the incumbent really stopped.
        On success the effective budget is bound to the release in the journal so every later retained
        launch of it (rollback, boot, restart) is judged under the same policy."""
        spec = manifest["spec"]
        rid = manifest["release_id"]
        budget = self.effective_budget(spec, op["payload"].get("effective_budget"), rid)
        plan = self.launch_admission(
            spec,
            rid,
            budget,
            stage="admission",
            provisional=True,
            memory_code="PREFLIGHT_MEMORY",
            metadata_code="PREFLIGHT_COMPAT",
        )
        self.j.set(f"effective_budget:{rid}", budget)
        return plan

    def fresh_platform_check(self, spec: dict[str, Any]) -> str | None:
        """Re-read the inventory now and compare it with the spec's pinned tuple. None when the release
        may run on this device (or the gate does not apply); otherwise the reason. Never disrupts."""
        if not self.enforce_platform:
            return None
        inv = self.inventory_reader() if self.inventory_reader else self.inventory
        from .compat import tuple_mismatch

        target = (spec.get("platform") or {}).get("target") or {}
        reason, _ = tuple_mismatch(target if isinstance(target, dict) else {}, inv)
        return (
            f"platform tuple: {reason}; a release built for another track cannot run here" if reason else None
        )

    def _platform_gate(self, spec: dict[str, Any], inventory: dict[str, Any], stage: str) -> dict[str, Any]:
        """The release's pinned platform tuple must be proven by THIS device's inventory (tracks that
        share a profile differ only here). Raises PREFLIGHT_COMPAT on a mismatch or an unknown tuple."""
        from .compat import tuple_mismatch

        target = (spec.get("platform") or {}).get("target") or {}
        target = target if isinstance(target, dict) else {}
        reason, warnings = tuple_mismatch(target, inventory)
        observed = {
            k: inventory.get(k) for k in ("arch", "l4t_release", "cuda_version", "compute_capability")
        }
        if reason:
            raise OpFailure(
                "PREFLIGHT_COMPAT",
                f"platform tuple: {reason}; a release built for another track cannot run here",
                stage,
                {"target": target, "observed": observed},
            )
        return {"target": target, "observed": observed, "warnings": warnings}

    # ------------------------------------------------------------------ staging
    def stage(self, op: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
        spec = manifest["spec"]
        model_path, tmpl_path, rt_dir, binary = self._paths(spec)
        f = spec["model"]["file"]
        t0 = time.monotonic()

        def progress(done: int, total: int) -> None:
            if int(time.monotonic() - t0) % 5 == 0:
                self.j.set_stage(
                    op["id"], "Staging", detail={"download_bytes": done, "download_total": total}
                )

        url = f["url"]

        if url.startswith("/api/"):
            url = self.server_base.rstrip("/") + url  # control-plane-relative artifact (simulator fixtures)

        try:
            dl = download_verified(
                self.client,
                url=url,
                dest=model_path,
                expected_sha256=f["sha256"],
                expected_size=int(f["size"]),
                auth=f.get("auth", "none"),
                server_base=self.server_base,
                progress=progress,
            )
        except DownloadError as e:
            if e.code == "INTERRUPTED":
                # agent shutdown, not a verdict on the release: surfaces like a transport failure so
                # the pre-grant row is deferred and resumed at restart (R40)
                raise Transient(str(e)) from e
            raise OpFailure(e.code, str(e), "staging", e.details) from e
        self.j.pin(str(model_path), f["sha256"], int(f["size"]), "staged", manifest["release_id"])
        self.emit("usage", "usage", {"ts": time.time(), "download_bytes": dl["bytes_downloaded"]})
        # GGUF metadata must match the manifest (physical and fixture alike)
        try:
            meta = gguf.qualification(gguf.read_metadata(str(model_path)))
        except gguf.GGUFError as e:
            raise OpFailure(
                "DIGEST_MISMATCH", f"downloaded file is not a readable GGUF: {e}", "staging"
            ) from e
        if tmpl_path:
            tmpl_path.write_text(spec["template"]["text"])
            if hashlib.sha256(spec["template"]["text"].encode()).hexdigest() != spec["template"]["sha256"]:
                raise OpFailure("DIGEST_MISMATCH", "template text does not match its pinned hash", "staging")
        # effective compatibility from the PARSED BYTES (supported architecture / quantization /
        # tokenizer, a template, sane integer dimensions) and every material manifest claim (header-
        # derived or operator-supplied, advisory) must agree with the sha256-verified bytes; a
        # contradiction means the manifest cannot be trusted for this file. Refused here, before the
        # grant, while the incumbent keeps serving.
        meta["compat"] = self.compat_check(spec, meta, "staging")
        if rt_dir and not self.simulated:
            self._stage_runtime(op, spec, rt_dir)
        elif rt_dir:
            self._stage_runtime(op, spec, rt_dir)
        return {
            "download": dl,
            "gguf": meta,
            "model_path": str(model_path),
            "template_path": str(tmpl_path) if tmpl_path else None,
        }

    def _stage_runtime(self, op: dict[str, Any], spec: dict[str, Any], rt_dir: Path) -> None:
        art_sha = spec["runtime"]["artifact_sha256"]
        files = spec["runtime"].get("artifact_files") or []
        if rt_dir.exists():
            for entry in files:
                p = rt_dir / entry["path"]
                if not p.exists() or sha256_file(p)[0] != entry.get("sha256"):
                    break
            else:
                return  # already staged and verified
        archive = self.data_dir / "cache" / "runtime" / f"{art_sha}.tar.gz"
        art_id = self._artifact_id_from_manifest(op)
        if not archive.exists() and art_id:
            url = f"{self.server_base.rstrip('/')}/api/agent/v1/runtime-artifacts/{art_id}/archive"
            try:
                download_verified(
                    self.client,
                    url=url,
                    dest=archive,
                    expected_sha256=art_sha,
                    expected_size=self._artifact_size(op),
                    auth="device",
                    server_base=self.server_base,
                )
            except DownloadError as e:
                if e.code == "DIGEST_MISMATCH" and e.details.get("expected_size") is None:
                    raise
                raise OpFailure(e.code, f"runtime artifact: {e}", "staging", e.details) from e
        if not archive.exists():
            raise OpFailure(
                "RUNTIME_ARTIFACT_MISSING",
                "runtime artifact archive is not on this device and not downloadable (device-scoped artifact?)",
                "staging",
            )
        try:
            extract_archive(archive, rt_dir, files)
        except DownloadError as e:
            raise OpFailure(e.code, f"runtime artifact: {e}", "staging", e.details) from e
        self.j.pin(str(archive), art_sha, archive.stat().st_size, "runtime", None)

    def _artifact_id_from_manifest(self, op: dict[str, Any]) -> str | None:
        m = self._cached_manifest(op["payload"]["target_release_id"])
        return (m or {}).get("runtime_artifact_id")

    def _artifact_size(self, op: dict[str, Any]) -> int:
        m = self._cached_manifest(op["payload"]["target_release_id"]) or {}
        return int(m.get("artifact_size") or 0) or 0

    # ------------------------------------------------------------------ grant
    def request_grant(self, op: dict[str, Any], live_seq: int, boot_id: str) -> tuple[dict[str, Any], float]:
        p = op["payload"]
        nonce = self.j.get(f"nonce:{op['id']}") or secrets.token_hex(12)
        self.j.set(f"nonce:{op['id']}", nonce)
        body = {
            "nonce": nonce,
            "boot_id": boot_id,
            "seq": live_seq,
            "active_release_id": self.active_release(),
            "recovery_release_id": self.recovery_release(),
            "release_digest": p.get("release_digest"),
            "plan_digest": p.get("plan_digest"),
            "artifact_sha256": p.get("artifact_sha256"),
        }
        t_req = time.monotonic()
        g = self.client.post(f"/api/agent/v1/operations/{op['id']}/grant", body, retries=1)
        return g, t_req

    # ------------------------------------------------------------------ cutover
    def cutover(
        self,
        op: dict[str, Any],
        manifest: dict[str, Any],
        staged: dict[str, Any],
        grant_deadline: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        spec = manifest["spec"]
        model_path, tmpl_path, rt_dir, binary = self._paths(spec)
        lib_dir = rt_dir / "lib" if (rt_dir and (rt_dir / "lib").exists()) else rt_dir
        if grant_deadline is not None:
            # FINAL monotonic TTL check after ALL blocking preparation (journal transactions and the
            # inventory read included), immediately before the first disruptive effect; nothing above
            # this line disrupts anything
            t_req, ttl = grant_deadline
            elapsed = time.monotonic() - t_req
            if elapsed > ttl:
                raise GrantExpiredBeforeDisruption(ttl, elapsed)
        self.gw.set_mode("closed")
        t0 = time.monotonic()
        stop = self.sup.stop()
        if not stop.get("stopped"):
            raise OpFailure(
                "RUNTIME_STOP_FAILED",
                "previous runtime did not exit; refusing to start a second runtime",
                "cutover",
                stop,
            )
        # FINAL launch admission: the incumbent has really exited, so the fresh MemAvailable read is
        # the memory the candidate gets; the pinned bytes are re-read and the release's own bound
        # budget applies (the same gate every retained launch passes through)
        rid = manifest["release_id"]
        budget = self.effective_budget(spec, op["payload"].get("effective_budget"), rid)
        try:
            final = self.launch_admission(
                spec,
                rid,
                budget,
                stage="cutover",
                memory_code="CUTOVER_MEMORY",
                metadata_code="PREFLIGHT_COMPAT",
            )
        except OpFailure as e:
            e.details = {**e.details, "stop": stop, "candidate_started": False}
            raise
        stop = {**stop, "mem_available_after_stop_mb": final["mem_available_mb"]}
        try:
            ev = self.sup.start(
                release_id=manifest["release_id"],
                spec=spec,
                model_path=model_path,
                template_path=tmpl_path,
                binary=binary,
                lib_dir=lib_dir,
            )
        except RuntimeError_ as e:
            raise OpFailure(e.code, str(e), "cutover", e.details) from e
        cutover_ms = round((time.monotonic() - t0) * 1000, 1)
        if not self.simulated and ev.get("intended_backend_ok") is not True:
            raise OpFailure(
                "RUNTIME_BACKEND_MISMATCH",
                "runtime did not report full CUDA offload (intended backend evidence missing or CPU fallback)",
                "cutover",
                {"evidence": ev},
            )
        exp = self._executable_sha(spec)
        if exp and not self.simulated and ev.get("binary_sha256") != exp:
            raise OpFailure(
                "DIGEST_MISMATCH", "running binary hash differs from the artifact manifest", "cutover"
            )
        if self.simulated:
            ev["binary_sha256"] = (
                exp  # simulated runtime: reports the artifact's executable member hash it stands in for
            )
        return {"cutover_ms": cutover_ms, "runtime": ev, "stop": stop, "launch_admission": final}

    # ------------------------------------------------------------------ eval / probation
    def _stage_plan(self, op: dict[str, Any], plan_id: str, plan_digest: str | None) -> dict[str, Any]:
        """R35: the immutable plan document is fetched and digest-verified BEFORE cutover and cached on
        disk, so nothing after the disruptive boundary depends on the network."""
        plan = self.client.get(f"/api/agent/v1/plans/{plan_id}")
        if plan.get("digest") != plan_digest:
            raise OpFailure("PLAN_MISMATCH", "plan digest differs from the operation binding", "staging")
        cached = self.data_dir / "cache" / "plans"
        cached.mkdir(parents=True, exist_ok=True)
        path = cached / f"{plan_digest}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan))
        tmp.replace(path)
        return plan

    def _cached_plan(self, plan_digest: str | None) -> dict[str, Any] | None:
        if not plan_digest:
            return None
        path = self.data_dir / "cache" / "plans" / f"{plan_digest}.json"
        try:
            plan = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        return plan if plan.get("digest") == plan_digest else None

    def run_eval(
        self,
        op: dict[str, Any],
        plan: dict[str, Any],
        release_id: str,
        release_digest: str,
        runtime_ev: dict[str, Any],
        stage: str = "eval",
    ) -> dict[str, Any]:
        """Run a staged (already digest-verified) plan through the gateway in eval mode."""
        if plan.get("digest") != op["payload"].get("plan_digest"):
            raise OpFailure("PLAN_MISMATCH", "plan digest differs from the operation binding", stage)
        self.gw.set_mode("eval")
        try:
            h = Harness(self.gw, self.sensors, device_id=self.device_id, simulated=self.simulated)
            res = h.run(
                plan,
                release_id=release_id,
                release_digest=release_digest,
                runtime_evidence=runtime_ev,
                operation_id=op["id"],
                stage=stage,
                cancel=self.cancel,
                generation=op.get("generation"),
            )
        finally:
            self.gw.set_mode("closed")
        seq = self.emit("critical", "eval_result", res)
        # the success outcome cites this record: remember its lane sequence so success is only posted
        # after the server has committed the evidence (evidence-before-success ordering)
        self.j.set_stage(op["id"], "Evaluating", detail={"eval_spool_seq": seq, "eval_result_id": res["id"]})
        return res

    def probation(self, op: dict[str, Any], sample_policy: dict[str, Any]) -> dict[str, Any]:
        """R41: only successful production completions count towards `probation_min_requests`;
        failures/timeouts are counted over the same production population. Rejected (4xx), eval-mode
        and warmup requests never count (the snapshot is taken after production opens)."""
        min_s = float(sample_policy.get("probation_min_s", 60))
        min_req = int(sample_policy.get("probation_min_requests", 0))
        self.gw.set_mode("production")
        start = dict(self.gw.stats)
        t0 = time.monotonic()
        checks = 0
        while True:
            elapsed = time.monotonic() - t0
            if self.cancel.is_set():
                raise OpFailure("INTERRUPTED", "probation interrupted by shutdown", "probation")
            if not self.sup.health() or self.gw.needs_restart:
                raise OpFailure(
                    "HEALTH_FAILED",
                    "runtime health failed during probation"
                    + (" (gateway flagged a controlled restart)" if self.gw.needs_restart else ""),
                    "probation",
                    {"elapsed_s": round(elapsed, 1), "needs_restart": self.gw.needs_restart},
                )
            checks += 1
            cur = self.gw.stats
            served = cur["served"] - start["served"]
            failed = (cur["failed"] + cur["timeouts"]) - (start["failed"] + start["timeouts"])
            if elapsed >= min_s and served >= min_req:
                attempted = served + failed
                return {
                    "elapsed_s": round(elapsed, 1),
                    "requests_served": served,
                    "requests_failed": failed,
                    "requests_attempted": attempted,
                    "failure_rate": round(failed / attempted, 4) if attempted else None,
                    "requests_rejected": cur["rejected"]
                    - start["rejected"],  # not counted; shown for honesty
                    "health_checks": checks,
                    "min_s": min_s,
                    "min_requests": min_req,
                    "population": "production_completions",
                }
            time.sleep(min(1.0, max(0.05, min_s / 20)))

    # ------------------------------------------------------------------ recovery
    def _recovery_identity(self, op: dict[str, Any]) -> str | None:
        """R38: the operation-bound incumbent recorded with the cutover intent wins over the global
        pointer; the pointer is only a fallback for rows written without an intent."""
        row = self.j.operation(op["id"]) or {}
        intent = (row.get("detail") or {}).get("intent") or {}
        if "recovery" in intent:
            return intent["recovery"]
        return self.recovery_release()

    def recover(self, op: dict[str, Any], reason: OpFailure) -> dict[str, Any]:
        """Restore the exact retained recovery release. The attempt bound (RECOVERY_ATTEMPTS) is durable:
        attempts resume from the journal row, so restarts never grant fresh attempts (R42). Sets the
        journal pointers; the caller commits the terminal row with the complete outcome."""
        rec = self._recovery_identity(op)
        self.j.set_stage(
            op["id"],
            "Recovering",
            detail={"failure": {"code": reason.code, "stage": reason.stage, "message": str(reason)}},
        )
        self.gw.set_mode("closed")
        degraded_kv = {
            "active_release_id": None,
            "health": "failed",
            "failed_generation_latch": op["generation"],
        }
        if not rec:
            self.sup.stop()
            self.j.set_many(degraded_kv)
            return {
                "recovered": False,
                "degraded": True,
                "attempts": 0,
                "reason": "no recovery release retained",
            }
        m = self._cached_manifest(rec)
        if m is None:
            try:
                m = self._manifest(rec)
            except Exception:
                m = None
        if m is None:
            self.sup.stop()
            self.j.set_many(degraded_kv)
            return {
                "recovered": False,
                "degraded": True,
                "attempts": 0,
                "reason": "recovery manifest unavailable",
            }
        spec = m["spec"]
        model_path, tmpl_path, rt_dir, binary = self._paths(spec)
        row = self.j.operation(op["id"]) or {}
        attempts = int(row.get("attempts") or 0)
        last = None
        while attempts < RECOVERY_ATTEMPTS:
            attempts += 1
            self.j.set_stage(
                op["id"], "Recovering", detail={"recovery_attempt": attempts}, bump_attempts=True
            )
            self.sup.stop()
            try:
                if not model_path.exists() or sha256_file(model_path)[0] != spec["model"]["file"]["sha256"]:
                    raise RuntimeError_(
                        "RECOVERY_PACKAGE_MISSING", "retained recovery package is missing or corrupt"
                    )
                mismatch = self.fresh_platform_check(spec)
                if mismatch:
                    raise RuntimeError_("PREFLIGHT_COMPAT", f"retained recovery release refused: {mismatch}")
                # the retained release is judged like any launch: its bytes (hash re-verified above),
                # its OWN bound budget and MemAvailable read now that nothing of ours is running
                try:
                    plan = self.launch_admission(
                        spec, rec, self.effective_budget(spec, None, rec), stage="recovering"
                    )
                except OpFailure as e:
                    raise RuntimeError_(e.code, f"retained recovery release refused: {e}", e.details) from e
                self.j.set_stage(op["id"], "Recovering", detail={"launch_admission": plan})
                gen_before = self.sup.generation
                ev = self.sup.start(
                    release_id=rec,
                    spec=spec,
                    model_path=model_path,
                    template_path=tmpl_path,
                    binary=binary,
                    lib_dir=(rt_dir / "lib" if rt_dir and (rt_dir / "lib").exists() else rt_dir),
                )
                if self.sup.generation <= gen_before or not self.sup.health():
                    raise RuntimeError_(
                        "HEALTH_FAILED", "recovery runtime did not verify as a fresh healthy child"
                    )
                if self.simulated:
                    ev["binary_sha256"] = self._executable_sha(spec)
                self.j.set_many(
                    {
                        "active_release_id": rec,
                        "health": "ok",
                        "failed_generation_latch": op["generation"],
                        "runtime_evidence": ev,
                    }
                )
                self.gw.needs_restart = False  # R37: cleared only after a verified fresh child + health
                self.gw.set_mode("production")
                return {
                    "recovered": True,
                    "degraded": False,
                    "attempts": attempts,
                    "active_release_id": rec,
                    "launch_admission": plan,
                }
            except RuntimeError_ as e:
                last = {"code": e.code, "message": str(e), "details": e.details}
                self._log("error", f"recovery attempt {attempts} failed: {e}", op["id"])
        self.sup.stop()
        self.j.set_many(degraded_kv)
        return {"recovered": False, "degraded": True, "attempts": attempts, "last": last}

    def _rollback(
        self,
        op: dict[str, Any],
        f: OpFailure,
        grant: dict[str, Any] | None,
        consumed_seq: int | None,
    ) -> dict[str, Any]:
        """Bounded recovery after the disruptive boundary: rollback to the retained release, or a
        Degraded shutdown; either way a terminal row with the COMPLETE outcome is committed (R33/R35)."""
        try:
            rec = self.recover(op, f)
        except Exception as e:  # storage/runtime failure inside recovery: degrade explicitly, never strand
            log.exception("recovery itself failed for %s", op["id"])
            self.gw.set_mode("closed")
            try:
                self.sup.stop()
            except Exception:
                pass
            rec = {"recovered": False, "degraded": True, "error": f"{type(e).__name__}: {str(e)[:200]}"}
            try:
                self.j.set_many(
                    {
                        "active_release_id": None,
                        "health": "failed",
                        "failed_generation_latch": op["generation"],
                    }
                )
            except Exception:
                pass
        failure = self._failure(op["id"], f.code, f.stage, str(f), {**f.details, "recovery": rec})
        outcome = {
            "status": "failed",
            "grant_id": (grant or {}).get("grant_id"),
            "grant_consumed_seq": consumed_seq,
            "failure": failure,
            "result": {"active_release_id": self.active_release(), "recovery": rec},
        }
        self.j.finish_operation(
            op["id"],
            "RolledBack" if rec.get("recovered") else "Degraded",
            outcome,
            {"generation": op["generation"], "failed_generation_latch": op["generation"]},
            acked=bool(op.get("local")),
        )
        return outcome

    def recover_local(self, reason: OpFailure, generation: int | None = None) -> dict[str, Any]:
        """R36: device-originated recovery (runtime failed to start on boot, or a controlled restart
        failed) through the same journaled, attempt-bounded path as an operation, on a REAL local row
        (never a synthetic id). Local rows are never posted to the server."""
        gen = int(generation if generation is not None else (self.j.get("generation", 0) or 0))
        op = {
            "id": f"local-{secrets.token_hex(6)}",
            "type": "recover_local",
            "payload": {"reason": reason.code, "active_release_id": self.active_release()},
            "generation": gen,
            "local": True,
        }
        self.j.begin_operation(op, local=True)
        self.j.set_stage(
            op["id"],
            "Recovering",
            detail={"intent": {"target": None, "recovery": self.recovery_release(), "kind": "local"}},
        )
        outcome = self._rollback(op, reason, None, None)
        return {**outcome["result"]["recovery"], "operation_id": op["id"]}

    # ------------------------------------------------------------------ main entry
    def execute(
        self, op: dict[str, Any], *, live_seq_getter, boot_id: str, report_progress
    ) -> dict[str, Any]:
        """Run one delivered operation to a terminal outcome dict (status, result, evidence, failure).
        Never raises for operational failures: every path commits a terminal row whose outcome is the
        exact document returned (and later replayed) to the server."""
        self.current = op
        try:
            try:
                if op["type"] == "deploy":
                    return self._deploy(op, live_seq_getter, boot_id, report_progress)
                if op["type"] == "recover":
                    return self._recover_op(op, live_seq_getter, boot_id, report_progress)
                if op["type"] == "eval":
                    return self._eval_op(op, live_seq_getter, boot_id, report_progress)
                if op["type"] == "health":
                    return self._health_op(op)
                if op["type"] == "collect":
                    return self._collect_op(op)
                self.j.begin_operation(op)
                return self._fail(op, "UNKNOWN_OPERATION", "start", f"unknown type {op['type']}")
            except Exception as e:  # R35: outer boundary; a non-terminal row is never left behind
                log.exception("operation %s raised outside its stage boundaries", op["id"])
                return self._unexpected(op, e)
        finally:
            self.current = None

    def _storage_degrade(
        self, op: dict[str, Any], e: Exception, extra: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Explicit safe handling when the journal cannot persist state (R35 follow-up): admission is
        closed and the owned runtime stopped, because an unrecorded runtime state must not keep
        serving; the returned outcome is marked non-durable and is never posted or acknowledged.
        Restart recovery settles the still non-terminal row from the durable journal."""
        log.error("journal storage failure during %s: %s", op["id"], e)
        self.gw.set_mode("closed")
        try:
            self.sup.stop()
        except Exception:
            log.exception("runtime stop failed during storage degrade")
        try:
            self.j.set_many({"health": "failed"})
        except Exception:
            pass
        failure = {
            "code": "STORAGE_ERROR",
            "stage": "terminal_commit",
            "message": f"{type(e).__name__}: {str(e)[:500]}",
            "details": {"admission": "closed", "runtime": self.sup.state()},
        }
        return {"status": "failed", "failure": failure, "durable": False, **(extra or {})}

    def _unexpected(self, op: dict[str, Any], e: Exception) -> dict[str, Any]:
        if isinstance(e, StorageError):
            return self._storage_degrade(op, e)
        code = "SERVER_UNREACHABLE" if isinstance(e, (ApiError, Transient)) else "UNEXPECTED_ERROR"
        row = self.j.operation(op["id"])
        stage = (row or {}).get("stage", "start").lower()
        # nothing disruptive happened outside _deploy's own boundary: keep serving if the runtime is fine
        if self.sup.state() == "running" and self.sup.health() and self.gw.mode != "production":
            self.gw.set_mode("production")
        failure = self._failure(op["id"], code, stage, f"{type(e).__name__}: {str(e)[:500]}")
        outcome = {
            "status": "failed",
            "failure": failure,
            "result": {"active_release_id": self.active_release()},
        }
        if row is not None and not row.get("terminal"):
            try:
                self.j.finish_operation(op["id"], "Failed", outcome)
            except Exception:
                log.exception("could not commit the terminal row for %s", op["id"])
        return outcome

    def _fail(
        self,
        op: dict[str, Any],
        code: str,
        stage: str,
        message: str,
        details: dict[str, Any] | None = None,
        *,
        journal_stage: str = "Failed",
        extra: dict[str, Any] | None = None,
        kv: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Commit a terminal failure with the complete wire outcome (R33) and return that same document."""
        outcome = {"status": "failed", "failure": self._failure(op["id"], code, stage, message, details)}
        outcome.update(extra or {})
        self.j.finish_operation(op["id"], journal_stage, outcome, kv)
        return outcome

    def _deferred_by_shutdown(self, op: dict[str, Any], stage: str, err: Exception) -> dict[str, Any] | None:
        """A transport failure while the agent is stopping, in a PRE-GRANT stage, is not the operation's
        failure: nothing was disrupted, so the row keeps its stage (restart recovery resumes it, R40),
        no terminal outcome is written or posted, and the caller returns `deferred`. After the grant the
        disruptive path runs to its own terminal outcome as before."""
        if not self.cancel.is_set():
            return None
        row = self.j.operation(op["id"]) or {}
        if row.get("terminal") or row.get("stage") not in self.PRE_GRANT_STAGES:
            return None
        self.j.set_stage(
            op["id"],
            row["stage"],
            detail={"deferred_by_shutdown": {"at": time.time(), "stage": stage, "error": str(err)[:200]}},
        )
        self._log(
            "warn", f"operation deferred by agent shutdown during {stage}; resumed at restart", op["id"]
        )
        return {"status": "deferred", "reason": "shutdown", "stage": stage}

    def _grant_binding_error(self, g: dict[str, Any], op: dict[str, Any], boot_id: str) -> str | None:
        """R34: identical binding checks for every grant-consuming flow (deploy, recover, eval). The
        grant mirrors the operation payload (server `_grant_wire`): target (None for eval), release and
        plan digests, generation, plus this boot and our nonce."""
        p = op.get("payload") or {}
        if not g.get("grant_id"):
            return "grant has no id"
        if g.get("operation_id") not in (None, op["id"]):
            return "grant belongs to another operation"
        if g.get("boot_id") != boot_id:
            return "grant boot id differs from this boot"
        if g.get("generation") != op["generation"]:
            return "grant generation differs from the operation"
        if g.get("target_release_id") != p.get("target_release_id"):
            return "grant target differs from the operation target"
        for key in ("release_digest", "plan_digest"):
            if key in g and p.get(key) is not None and g.get(key) != p.get(key):
                return f"grant {key} differs from the operation binding"
        nonce = self.j.get(f"nonce:{op['id']}")
        if g.get("nonce") is not None and nonce is not None and g.get("nonce") != nonce:
            return "grant nonce differs from the request"
        return None

    def _grant_phase(
        self, op: dict[str, Any], live_seq_getter, boot_id: str
    ) -> tuple[dict[str, Any] | None, float, dict[str, Any] | None]:
        """Request and validate a grant with fresh bindings. Returns (grant, t_req, None) on success or
        (None, 0, outcome) when the operation ended here: refused/cancelled/binding -> terminal failure;
        423 (paused / outside the window) -> the row stays pre-grant and `deferred` is returned so the
        server's next delivery retries honestly (never acknowledged or discarded)."""
        try:
            g, t_req = self.request_grant(op, live_seq_getter(), boot_id)
        except ApiError as e:
            if e.status == 423:
                self.j.set_stage(
                    op["id"],
                    "WaitingGrant",
                    detail={
                        "grant_deferred": {"status": e.status, "at": time.time(), "body": str(e.body)[:200]}
                    },
                )
                self._log("warn", f"grant deferred by the server (HTTP 423): {str(e.body)[:200]}", op["id"])
                return None, 0.0, {"status": "deferred", "reason": "grant_deferred", "http": e.status}
            cancelled = e.status == 409 and "cancel" in str(e.body).lower()
            return (
                None,
                0.0,
                self._fail(
                    op,
                    "GRANT_CANCELLED" if cancelled else "GRANT_REFUSED",
                    "waiting_grant",
                    f"server refused the grant: {e.body}",
                    journal_stage="Cancelled" if cancelled else "Failed",
                ),
            )
        except Transient as e:
            deferred = self._deferred_by_shutdown(op, "waiting_grant", e)
            if deferred is not None:
                return None, 0.0, deferred
            return None, 0.0, self._fail(op, "SERVER_UNREACHABLE", "waiting_grant", str(e))
        bad = self._grant_binding_error(g, op, boot_id)
        if bad:
            return (
                None,
                0.0,
                self._fail(op, "GRANT_BINDING", "waiting_grant", f"grant bindings do not match: {bad}"),
            )
        return g, t_req, None

    def _deploy(self, op, live_seq_getter, boot_id, report_progress) -> dict[str, Any]:
        p = op["payload"]
        self.j.begin_operation(op)
        target = p["target_release_id"]
        plan_doc: dict[str, Any] | None = None
        try:
            manifest = self._manifest(target)
            if manifest.get("digest") != p.get("release_digest"):
                raise OpFailure(
                    "DIGEST_MISMATCH", "release manifest digest differs from the operation binding", "staging"
                )
            plan = self.preflight(op, manifest)
            self.j.set_stage(op["id"], "Staging", detail={"preflight": plan})
            report_progress(op, {"stage": "staging", "preflight": plan})
            staged = self.stage(op, manifest)  # re-verifies already-staged bytes on a resumed row (R40)
            staged["admission"] = self.admission(
                op, manifest, staged
            )  # byte-authoritative, incumbent serving
            if p.get("plan_id"):
                plan_doc = self._stage_plan(op, p["plan_id"], p.get("plan_digest"))
                staged["plan_digest"] = plan_doc["digest"]
            self.j.set_stage(
                op["id"], "WaitingGrant", detail={"staged": {k: v for k, v in staged.items() if k != "gguf"}}
            )
            report_progress(op, {"stage": "staging", "admission": staged["admission"]})
            report_progress(op, {"stage": "waiting_grant"})
        except OpFailure as f:
            return self._fail(op, f.code, f.stage, str(f), f.details)
        except (ApiError, Transient) as e:
            deferred = self._deferred_by_shutdown(op, "staging", e)
            if deferred is not None:
                return deferred
            return self._fail(op, "SERVER_UNREACHABLE", "staging", str(e))
        # ---- grant (short TTL, monotonic) ----
        g, t_req, ended = self._grant_phase(op, live_seq_getter, boot_id)
        if ended is not None or g is None:
            return ended or self._fail(op, "GRANT_REFUSED", "waiting_grant", "no grant")
        ttl = float(g.get("ttl_s", 30))
        consumed_seq = live_seq_getter()
        prev_active = self.active_release()
        # persist consumption + cutover intent + the exact recovery identity in ONE transaction (R38),
        # BEFORE stopping anything
        self.j.set_stage(
            op["id"],
            "Cutover",
            grant=g,
            grant_consumed_seq=consumed_seq,
            started_monotonic=t_req,
            detail={
                "intent": {
                    "target": target,
                    "recovery": prev_active,
                    "consumed_seq": consumed_seq,
                    "grant_id": g["grant_id"],
                }
            },
        )
        grant_ref = {"grant_id": g["grant_id"], "grant_consumed_seq": consumed_seq}
        report_progress(op, {"stage": "cutover"})  # network wait happens BEFORE the final TTL check
        # authoritative platform re-check with a FRESH inventory read (may take seconds: nvidia-smi).
        # A mismatch here is NOT disruptive: the incumbent keeps serving and the recovery pointer is
        # untouched, so it is an ordinary failure without rollback. The TTL is re-checked after it.
        mismatch = self.fresh_platform_check(manifest["spec"])
        if mismatch:
            return self._fail(
                op,
                "PREFLIGHT_COMPAT",
                "cutover",
                mismatch + "; nothing was changed",
                {"disruption_started": False},
                extra=grant_ref,
            )
        if time.monotonic() - t_req > ttl:
            # grant aged out before we could start: do not start; nothing was changed
            return self._fail(
                op,
                "GRANT_EXPIRED",
                "cutover",
                "grant TTL elapsed (monotonic) before the cutover started; nothing was changed",
                {"ttl_s": ttl, "elapsed_s": round(time.monotonic() - t_req, 3)},
                extra=grant_ref,
            )
        # ---- disruptive boundary: from here on nothing waits on the network until the candidate is up ----
        # the global recovery pointer moves to the incumbent only now that disruption really starts; an
        # expired grant above leaves the previously retained release untouched (R34/R38 follow-up). The
        # operation-bound intent committed above already names the incumbent for restart recovery.
        prior_recovery = self.recovery_release()
        self.j.set_stage(
            op["id"],
            "Cutover",
            detail={"disruption_started": True, "prior_recovery": prior_recovery},
            kv_updates={"recovery_release_id": prev_active},
        )
        try:
            try:
                cut = self.cutover(op, manifest, staged, grant_deadline=(t_req, ttl))
                self.j.set_many(
                    {"active_release_id": target, "health": "ok", "runtime_evidence": cut["runtime"]}
                )
                self.j.pin(
                    staged["model_path"],
                    manifest["spec"]["model"]["file"]["sha256"],
                    int(manifest["spec"]["model"]["file"]["size"]),
                    "active",
                    target,
                )
                evidence: dict[str, Any] = {
                    "health": "ok",
                    "cutover_ms": cut["cutover_ms"],
                    "runtime": cut["runtime"],
                    "stop": cut["stop"],
                    "admission": cut["launch_admission"],
                }
                if p.get("plan_id"):
                    plan_doc = plan_doc or self._cached_plan(p.get("plan_digest"))
                    if plan_doc is None:
                        raise OpFailure("PLAN_MISMATCH", "staged plan document is missing", "evaluating")
                    self.j.set_stage(op["id"], "Evaluating")
                    self._progress_quiet(report_progress, op, {"stage": "evaluating"})
                    res = self.run_eval(op, plan_doc, target, p["release_digest"], cut["runtime"])
                    evidence["eval"] = {
                        "verdict": res["device_verdict"],
                        "plan_digest": res["plan_digest"],
                        "eval_result_id": res["id"],
                        "gates": res["gates"],
                        "coverage": res["coverage"],
                    }
                    if res["device_verdict"] != "passed":
                        raise OpFailure(
                            "EVAL_FAILED" if res["device_verdict"] == "failed" else "EVAL_INCONCLUSIVE",
                            f"eval gate verdict: {res['device_verdict']}",
                            "evaluating",
                            {"gates": res["gates"]},
                        )
                    sp = plan_doc.get("sample_policy") or {}
                else:
                    sp = {"probation_min_s": 5, "probation_min_requests": 0}
                self.j.set_stage(op["id"], "Probation")
                self._progress_quiet(report_progress, op, {"stage": "probation"})
                evidence["probation"] = self.probation(op, sp)
            except GrantExpiredBeforeDisruption as f:
                # the transaction above blocked past the TTL: nothing was disrupted, so restore the prior
                # retained recovery pointer (the durable operation intent stays as committed) and fail
                try:
                    self.j.set_stage(
                        op["id"],
                        "Cutover",
                        detail={"disruption_started": False, "expired_after_preparation": True},
                        kv_updates={"recovery_release_id": prior_recovery},
                    )
                except Exception as e:
                    return self._storage_degrade(op, e, grant_ref)
                return self._fail(op, f.code, f.stage, str(f), f.details, extra=grant_ref)
            except OpFailure:
                raise
            except (ApiError, Transient) as e:
                raise OpFailure("SERVER_UNREACHABLE", str(e), self._stage_of(op)) from e
            except RuntimeError_ as e:
                raise OpFailure(e.code, str(e), self._stage_of(op), e.details) from e
            except Exception as e:
                raise OpFailure(
                    "STORAGE_ERROR" if isinstance(e, StorageError) else "UNEXPECTED_ERROR",
                    f"{type(e).__name__}: {str(e)[:500]}",
                    self._stage_of(op),
                ) from e
        except OpFailure as f:
            return self._rollback(op, f, g, consumed_seq)
        outcome = {
            "status": "succeeded", **grant_ref,
            "result": {"active_release_id": target, "release_digest": p["release_digest"], "generation": op["generation"], "recovery_release_id": prev_active},
            "evidence": evidence,
        }  # fmt: skip
        try:
            self.j.finish_operation(
                op["id"],
                "Succeeded",
                outcome,
                {
                    "active_release_id": target,
                    "recovery_release_id": prev_active,
                    "health": "ok",
                    "failed_generation_latch": None,
                    "generation": op["generation"],
                },
            )
        except Exception as e:  # the terminal commit itself failed: the success is not durable
            return self._storage_degrade(op, e, grant_ref)
        try:
            self._gc_pins(keep={target, prev_active})  # best-effort cleanup after a durable success
        except Exception:
            log.exception("pin garbage collection failed after a durable success; outcome unchanged")
        return outcome

    def _stage_of(self, op: dict[str, Any]) -> str:
        row = self.j.operation(op["id"]) if not self.j.broken else None
        return str((row or {}).get("stage") or "cutover").lower()

    @staticmethod
    def _progress_quiet(report_progress, op: dict[str, Any], progress: dict[str, Any]) -> None:
        """Progress after cutover is best-effort: a lost server never turns into a stranded runtime."""
        try:
            report_progress(op, progress)
        except Exception:
            pass

    def _recover_op(self, op, live_seq_getter, boot_id, report_progress) -> dict[str, Any]:
        p = op["payload"]
        self.j.begin_operation(op)
        target = p["target_release_id"]
        if target != self.recovery_release():
            return self._fail(
                op,
                "RECOVERY_MISMATCH",
                "staging",
                f"operation targets {target}, device retains {self.recovery_release()}",
            )
        try:
            m = self._cached_manifest(target) or self._manifest(target)
        except (ApiError, Transient) as e:
            deferred = self._deferred_by_shutdown(op, "staging", e)
            if deferred is not None:
                return deferred
            return self._fail(op, "SERVER_UNREACHABLE", "staging", str(e))
        model_path, tmpl_path, rt_dir, binary = self._paths(m["spec"])
        if not model_path.exists() or sha256_file(model_path)[0] != m["spec"]["model"]["file"]["sha256"]:
            return self._fail(
                op, "RECOVERY_PACKAGE_MISSING", "staging", "retained recovery package is missing or corrupt"
            )
        # provisional launch admission BEFORE the grant: the retained bytes must still qualify and the
        # release must fit under its effective budget (payload policy, bound for later retained
        # launches); refused here nothing is disrupted and the incumbent keeps serving
        budget = self.effective_budget(m["spec"], p.get("effective_budget"), target)
        try:
            pre = self.launch_admission(m["spec"], target, budget, stage="staging", provisional=True)
        except OpFailure as f:
            return self._fail(
                op, f.code, "staging", f"{f}; nothing was changed", {**f.details, "disruption_started": False}
            )
        self.j.set(f"effective_budget:{target}", budget)
        self.j.set_stage(op["id"], "WaitingGrant", detail={"admission": pre})
        g, t_req, ended = self._grant_phase(op, live_seq_getter, boot_id)  # R34: same checks as deploy
        if ended is not None or g is None:
            return ended or self._fail(op, "GRANT_REFUSED", "waiting_grant", "no grant")
        ttl = float(g.get("ttl_s", 30))
        consumed_seq = live_seq_getter()
        prev = self.active_release()
        self.j.set_stage(
            op["id"],
            "Cutover",
            grant=g,
            grant_consumed_seq=consumed_seq,
            started_monotonic=t_req,
            detail={"intent": {"target": target, "recovery": target, "kind": "recover", "previous": prev}},
        )
        grant_ref = {"grant_id": g["grant_id"], "grant_consumed_seq": consumed_seq}
        report_progress(op, {"stage": "cutover"})
        mismatch = self.fresh_platform_check(m["spec"])  # fresh read BEFORE the TTL check and any disruption
        if mismatch:
            return self._fail(
                op,
                "PREFLIGHT_COMPAT",
                "cutover",
                mismatch + "; nothing was changed",
                {"disruption_started": False},
                extra=grant_ref,
            )
        if time.monotonic() - t_req > ttl:  # final monotonic check immediately before the first disruption
            return self._fail(
                op,
                "GRANT_EXPIRED",
                "cutover",
                "grant TTL elapsed (monotonic) before the recovery cutover started; nothing was changed",
                {"ttl_s": ttl, "elapsed_s": round(time.monotonic() - t_req, 3)},
                extra=grant_ref,
            )
        self.gw.set_mode("closed")
        t0 = time.monotonic()
        try:
            stop = self.sup.stop()
            if not stop.get("stopped"):
                raise RuntimeError_("RUNTIME_STOP_FAILED", "previous runtime did not exit", stop)
            # FINAL launch admission with MemAvailable read now that the previous child has stopped
            final = self.launch_admission(m["spec"], target, budget, stage="cutover")
            self.j.set_stage(op["id"], "Cutover", detail={"launch_admission": final})
            gen_before = self.sup.generation
            ev = self.sup.start(
                release_id=target,
                spec=m["spec"],
                model_path=model_path,
                template_path=tmpl_path,
                binary=binary,
                lib_dir=(rt_dir / "lib" if rt_dir and (rt_dir / "lib").exists() else rt_dir),
            )
            if self.sup.generation <= gen_before or not self.sup.health():
                raise RuntimeError_(
                    "HEALTH_FAILED", "recovery runtime did not verify as a fresh healthy child"
                )
        except Exception as e:
            code = e.code if isinstance(e, (RuntimeError_, OpFailure)) else "UNEXPECTED_ERROR"
            details = dict(e.details) if isinstance(e, (RuntimeError_, OpFailure)) else {}
            try:
                self.sup.stop()
            except Exception:
                pass
            return self._fail(
                op,
                code,
                "cutover",
                str(e),
                {**details, "runtime_started": False},
                journal_stage="Degraded",
                extra=grant_ref,
                kv={
                    "active_release_id": None,
                    "health": "failed",
                    "failed_generation_latch": op["generation"],
                },
            )
        if self.simulated:
            ev["binary_sha256"] = self._executable_sha(m["spec"])
        self.gw.needs_restart = False
        self.gw.set_mode("production")
        outcome = {
            "status": "succeeded",
            **grant_ref,
            "result": {
                "active_release_id": target,
                "release_digest": p["release_digest"],
                "generation": op["generation"],
                "recovery_release_id": prev,
            },
            "evidence": {
                "health": "ok",
                "cutover_ms": round((time.monotonic() - t0) * 1000, 1),
                "runtime": ev,
                "admission": final,
            },
        }
        self.j.finish_operation(
            op["id"],
            "Succeeded",
            outcome,
            {
                "active_release_id": target,
                "recovery_release_id": prev,
                "health": "ok",
                "runtime_evidence": ev,
                "generation": op["generation"],
                "failed_generation_latch": None,
            },
        )
        return outcome

    def _eval_op(self, op, live_seq_getter, boot_id, report_progress) -> dict[str, Any]:
        """Standalone eval is a MUTATING operation (it closes production for an exclusive run), so it
        owns the device through a grant exactly like deploy (R77): fresh bindings, immediate monotonic
        TTL check before production is closed, grant consumed in the outcome."""
        p = op["payload"]
        self.j.begin_operation(op)
        release_id = p.get("release_id")
        if self.active_release() != release_id or self.sup.state() != "running":
            return self._fail(
                op,
                "EVAL_ERROR",
                "evaluating",
                f"device runs {self.active_release()} ({self.sup.state()}), operation expects {release_id}",
            )
        try:
            plan = self._stage_plan(op, p["plan_id"], p.get("plan_digest"))  # before the grant, like bytes
        except OpFailure as f:
            return self._fail(op, f.code, f.stage, str(f), f.details)
        except (ApiError, Transient) as e:
            deferred = self._deferred_by_shutdown(op, "staging", e)
            if deferred is not None:
                return deferred
            return self._fail(op, "SERVER_UNREACHABLE", "staging", str(e))
        self.j.set_stage(op["id"], "WaitingGrant", detail={"staged": {"plan_digest": plan["digest"]}})
        report_progress(op, {"stage": "waiting_grant"})
        g, t_req, ended = self._grant_phase(op, live_seq_getter, boot_id)
        if ended is not None or g is None:
            return ended or self._fail(op, "GRANT_REFUSED", "waiting_grant", "no grant")
        ttl = float(g.get("ttl_s", 30))
        consumed_seq = live_seq_getter()
        self.j.set_stage(
            op["id"],
            "Evaluating",
            grant=g,
            grant_consumed_seq=consumed_seq,
            started_monotonic=t_req,
            detail={
                "intent": {
                    "target": release_id,
                    "recovery": release_id,
                    "kind": "eval",
                    "grant_id": g["grant_id"],
                }
            },
        )
        grant_ref = {"grant_id": g["grant_id"], "grant_consumed_seq": consumed_seq}
        report_progress(op, {"stage": "evaluating"})  # network wait BEFORE the final TTL check
        if time.monotonic() - t_req > ttl:
            return self._fail(
                op,
                "GRANT_EXPIRED",
                "evaluating",
                "grant TTL elapsed (monotonic) before production was closed; nothing was changed",
                {"ttl_s": ttl, "elapsed_s": round(time.monotonic() - t_req, 3)},
                extra=grant_ref,
            )
        try:
            res = self.run_eval(
                op,
                plan,
                release_id,
                p["release_digest"],
                self.j.get("runtime_evidence") or {},
                stage="baseline" if p.get("baseline") else "eval",
            )
        except OpFailure as f:
            self._reopen_production()
            return self._fail(op, f.code, f.stage, str(f), f.details, extra=grant_ref)
        except Exception as e:
            self._reopen_production()
            return self._fail(
                op,
                "STORAGE_ERROR" if isinstance(e, StorageError) else "UNEXPECTED_ERROR",
                "evaluating",
                f"{type(e).__name__}: {str(e)[:500]}",
                extra=grant_ref,
            )
        self._reopen_production()
        outcome = {
            "status": "succeeded",
            **grant_ref,
            "result": {
                "active_release_id": release_id,
                "eval_result_id": res["id"],
                "generation": op["generation"],
            },
            "evidence": {
                "eval": {
                    "verdict": res["device_verdict"],
                    "plan_digest": res["plan_digest"],
                    "eval_result_id": res["id"],
                    "gates": res["gates"],
                    "coverage": res["coverage"],
                },
                "runtime": self.j.get("runtime_evidence") or {},
            },
        }
        self.j.finish_operation(op["id"], "Succeeded", outcome)
        return outcome

    def _reopen_production(self) -> None:
        """After an exclusive eval the incumbent keeps serving; a flagged/unhealthy child stays closed
        for the agent's supervision (R37) instead of being exposed."""
        if self.sup.state() == "running" and not self.gw.needs_restart:
            self.gw.set_mode("production")
        else:
            self.gw.set_mode("closed")

    def _health_op(self, op) -> dict[str, Any]:
        self.j.begin_operation(op)
        ok = self.sup.health()
        props = self.sup.props() if ok else None
        outcome = {
            "status": "succeeded",
            "result": {"active_release_id": self.active_release()},
            "evidence": {
                "health": "ok" if ok else "failed",
                "gateway_mode": self.gw.mode,
                "runtime_state": self.sup.state(),
                "props": {k: props.get(k) for k in ("build_info", "n_ctx", "total_slots")} if props else None,
            },
        }
        self.j.finish_operation(op["id"], "Succeeded", outcome)
        return outcome

    def _collect_op(self, op) -> dict[str, Any]:
        self.j.begin_operation(op)
        tail = self.sup.log_tail(200)
        self.emit(
            "telemetry",
            "log",
            {
                "ts": time.time(),
                "level": "info",
                "source": "runtime",
                "message": "runtime log tail collected",
                "operation_id": op["id"],
                "attrs": {"lines": tail[-50:]},
            },
        )
        outcome = {
            "status": "succeeded",
            "result": {
                "active_release_id": self.active_release(),
                "lines": len(tail),
                "lanes": self.j.lane_status(),
            },
            "evidence": {"health": "ok" if self.sup.health() else "failed"},
        }
        self.j.finish_operation(op["id"], "Succeeded", outcome)
        return outcome

    def _gc_pins(self, keep: set[str | None]) -> None:
        for pin in self.j.pins():
            if pin["role"] in ("staged", "active") and pin["release_id"] not in keep:
                self.j.unpin_release(pin["release_id"])
                try:
                    Path(pin["path"]).unlink(missing_ok=True)
                except OSError:
                    pass

    # ------------------------------------------------------------------ restart recovery
    PRE_GRANT_STAGES = ("Staging", "WaitingGrant")

    def recover_after_restart(self) -> dict[str, Any] | None:
        """Called once at startup. An interrupted Cutover/Evaluating/Probation/Recovering row restores the
        operation-bound incumbent (R38) and commits the complete INTERRUPTED outcome for replay (R33).
        A pre-grant row (Staging/WaitingGrant) is RESUMABLE (R40): it is reset to Staging so the server's
        redelivery re-verifies the staged bytes and continues; it is never made terminal locally while
        the server may still own it."""
        op = self.j.current_operation()
        if op is None:
            return None
        if op["stage"] in self.PRE_GRANT_STAGES:
            self.j.set_stage(
                op["id"],
                "Staging",
                detail={
                    "resumed_after_restart": True,
                    "resumed_from": op["stage"],
                    "resumed_at": time.time(),
                },
            )
            return {"operation_id": op["id"], "action": "resumed", "from": op["stage"]}
        f = OpFailure("INTERRUPTED", f"agent restarted during {op['stage']}", op["stage"].lower())
        local = op["type"] == "recover_local" or op["id"].startswith("local-")
        outcome = self._rollback({**op, "local": local}, f, op.get("grant"), op.get("grant_consumed_seq"))
        rec = outcome["result"]["recovery"]
        return {
            "operation_id": op["id"],
            "action": "recovered",
            "result": rec,
            "grant": op.get("grant"),
            "consumed_seq": op.get("grant_consumed_seq"),
        }
