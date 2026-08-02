"""store.py — corpus loading + validation (port of src/runner/store.ts).

Loads scenarios, sealed answer keys (with content-hash verification), eval-set
configs (resolving scenario ids to files under scenarios/), and the quarantine
list. Pure fs + pydantic; no sibling-component dependencies, so the CLI's
lint/report paths work even before the sandbox/executors/graders/scoring land.

This module also carries the trial/artifact helpers from src/runner/trial-utils.ts
(the Python port owns only store/suite_runner/replay, so the sibling-free
helpers live here) and the small call-shape adapters the runner uses to talk
to the concurrently-written scoring/graders siblings.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from convoy_evals.runtime.log import load_events_jsonl
from convoy_evals.sandbox.api import GateScriptReport
from convoy_evals.schema.scenario import AnswerKey, EvalSetConfig, Scenario
from convoy_evals.schema.verdict import (
    NoteEvidence,
    SuiteResult,
    TrialResult,
    Verdict,
    VerdictScope,
)

DAY_MS = 86_400_000.0

# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_hex(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def normalize_hash(h: str) -> str:
    """Strip an optional "sha256:" / "sha256-" prefix and lowercase."""
    return re.sub(r"^sha256[:-]", "", h, flags=re.IGNORECASE).lower()


# ---------------------------------------------------------------------------
# JSON reading with pathful errors
# ---------------------------------------------------------------------------


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    except OSError as err:
        raise IOError("cannot read {0}: {1}".format(path, err))


def _read_json(path: str) -> Any:
    text = _read_text(path)
    try:
        return json.loads(text)
    except ValueError as err:
        raise ValueError("invalid JSON in {0}: {1}".format(path, err))


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


def load_scenario(path: str) -> Scenario:
    data = _read_json(path)
    try:
        return Scenario.model_validate(data)
    except Exception as err:  # pydantic.ValidationError
        raise ValueError("scenario {0} failed validation: {1}".format(path, err))


# ---------------------------------------------------------------------------
# Answer key
# ---------------------------------------------------------------------------


@dataclass
class AnswerKeyFile:
    key: AnswerKey
    # sha256 hex of the raw file bytes.
    content_hash: str
    # sha256 hex of the re-serialized (compact JSON) parsed content.
    canonical_hash: str


def load_answer_key_file(path: str) -> AnswerKeyFile:
    try:
        raw = _read_text(path)
    except IOError as err:
        raise IOError("cannot read answer key {0}: {1}".format(path, err))
    try:
        data = json.loads(raw)
    except ValueError as err:
        raise ValueError("invalid JSON in answer key {0}: {1}".format(path, err))
    try:
        key = AnswerKey.model_validate(data)
    except Exception as err:
        raise ValueError("answer key {0} failed validation: {1}".format(path, err))
    canonical = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return AnswerKeyFile(
        key=key, content_hash=sha256_hex(raw), canonical_hash=sha256_hex(canonical)
    )


def answer_key_hash_matches(file: AnswerKeyFile, expected_hash: str) -> bool:
    """True when the expected hash matches either the raw or canonical content hash."""
    want = normalize_hash(expected_hash)
    return want == file.content_hash or want == file.canonical_hash


def _default_warn(msg: str) -> None:
    print(msg, file=sys.stderr)


def load_answer_key(
    path: str,
    expected_hash: Optional[str] = None,
    warn: Callable[[str], None] = _default_warn,
) -> AnswerKey:
    """Load an answer key; when `expected_hash` is given, verify it against the
    file content and WARN (not raise) on mismatch — a drifted hash is an
    authoring bug the lint command turns into a hard error."""
    file = load_answer_key_file(path)
    if expected_hash is not None and not answer_key_hash_matches(file, expected_hash):
        warn(
            "[store] answer key hash mismatch for {0}: scenario expects {1}, "
            "content is sha256:{2}".format(path, expected_hash, file.content_hash)
        )
    return file.key


# ---------------------------------------------------------------------------
# Eval set
# ---------------------------------------------------------------------------


@dataclass
class LoadedEvalSet:
    config: EvalSetConfig
    set_path: str
    # The scenarios/ directory the set's ids resolve against.
    scenarios_dir: str
    # scenario id -> absolute scenario file path (resolved ids only).
    scenario_paths: Dict[str, str] = field(default_factory=dict)
    # Ids listed in the set with no matching *.scenario.json file.
    missing: List[str] = field(default_factory=list)


def load_eval_set(path: str) -> LoadedEvalSet:
    """Load an eval-set config and resolve each scenario id to a file. Sets live
    at scenarios/sets/<name>.json, scenarios at scenarios/<id>.scenario.json —
    filename convention first, then a content scan matching the declared `id`."""
    set_path = os.path.abspath(path)
    data = _read_json(set_path)
    try:
        config = EvalSetConfig.model_validate(data)
    except Exception as err:
        raise ValueError("eval set {0} failed validation: {1}".format(set_path, err))
    set_dir = os.path.dirname(set_path)
    scenarios_dir = os.path.dirname(set_dir) if os.path.basename(set_dir) == "sets" else set_dir

    by_stem: Dict[str, str] = {}
    if os.path.isdir(scenarios_dir):
        for f in sorted(os.listdir(scenarios_dir)):
            if f.endswith(".scenario.json"):
                by_stem[f[: -len(".scenario.json")]] = os.path.join(scenarios_dir, f)

    scenario_paths: Dict[str, str] = {}
    missing: List[str] = []
    for sid in config.scenarios:
        p = by_stem.get(sid)
        if p is None:
            for candidate in by_stem.values():
                try:
                    candidate_json = _read_json(candidate)
                except Exception:
                    continue  # unreadable candidate — lint reports it via schema check
                if isinstance(candidate_json, dict) and candidate_json.get("id") == sid:
                    p = candidate
                    break
        if p is not None:
            scenario_paths[sid] = p
        else:
            missing.append(sid)
    return LoadedEvalSet(
        config=config,
        set_path=set_path,
        scenarios_dir=scenarios_dir,
        scenario_paths=scenario_paths,
        missing=missing,
    )


def resolve_answer_key_path(ref: str, scenario_path: str, scenarios_dir: str) -> str:
    """Resolve a scenario's answerKeyRef.path. Tried in order: absolute; relative
    to the scenario file; relative to scenarios/; scenarios/keys/<basename>."""
    if os.path.isabs(ref):
        return ref
    candidates = [
        os.path.join(os.path.dirname(scenario_path), ref),
        os.path.join(scenarios_dir, ref),
        os.path.join(scenarios_dir, "keys", os.path.basename(ref)),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # Best guess — load_answer_key_file will raise a clear cannot-read error.
    return candidates[1]


# ---------------------------------------------------------------------------
# Quarantine — {quarantined: [...]} in a tiny YAML file. No YAML dep in the
# project, so this is a deliberately minimal parser for exactly that shape
# (block list, inline list, quoted strings, comments, `- id: x` rows).
# ---------------------------------------------------------------------------


def _unquote(v: str) -> str:
    t = v.strip()
    if (t.startswith('"') and t.endswith('"') and len(t) >= 2) or (
        t.startswith("'") and t.endswith("'") and len(t) >= 2
    ):
        return t[1:-1]
    return t


def parse_quarantine_yaml(text: str) -> Dict[str, List[str]]:
    quarantined: List[str] = []
    in_list = False
    for raw_line in re.split(r"\r?\n", text):
        line = re.sub(r"(^|\s)#.*$", "", raw_line, count=1).rstrip()
        if not line.strip():
            continue
        head = re.match(r"^quarantined:\s*(.*)$", line)
        if head:
            rest = (head.group(1) or "").strip()
            if rest.startswith("["):
                inner = re.sub(r"\]\s*$", "", re.sub(r"^\[", "", rest))
                for part in inner.split(","):
                    v = _unquote(part)
                    if v:
                        quarantined.append(v)
                in_list = False
            else:
                in_list = True
            continue
        if in_list:
            item = re.match(r"^-\s*(.+)$", line.strip())
            if item:
                v = _unquote(item.group(1) or "")
                # tolerate `- id: renewal-x` rows (richer quarantine entries)
                id_field = re.match(r"^id:\s*(.+)$", v)
                if id_field:
                    v = _unquote(id_field.group(1) or "")
                if v:
                    quarantined.append(v)
            elif not re.match(r"^\s", line):
                in_list = False  # a new top-level key ends the block list
    return {"quarantined": quarantined}


def load_quarantine(path: str) -> Dict[str, List[str]]:
    """Missing file -> empty quarantine (the corpus may not ship one yet)."""
    if not os.path.exists(path):
        return {"quarantined": []}
    return parse_quarantine_yaml(_read_text(path))


# ---------------------------------------------------------------------------
# Trial/artifact helpers (port of src/runner/trial-utils.ts). No sibling
# imports: everything below depends only on the co-signed schema/runtime files.
# ---------------------------------------------------------------------------


def sort_events(events: List[Any]) -> List[Any]:
    """Canonical grading order: (ts, seq) — matches EventLog.for_mission."""
    return sorted(events, key=lambda e: (e.ts, e.seq))


def _event_json(e: Any) -> Dict[str, Any]:
    """EventLog-style compact dump (exclude_none), except required-but-nullable
    fields (e.g. gate_raised.deadlineAt) are kept as explicit null — dropping
    them would make the line fail re-validation on replay (the TS harness also
    writes null for these)."""
    data = e.model_dump(exclude_none=True)
    for name, field_info in type(e).model_fields.items():
        if field_info.is_required() and name not in data:
            data[name] = None
    return data


def to_jsonl(events: List[Any]) -> str:
    lines = [json.dumps(_event_json(e), separators=(",", ":")) for e in events]
    return "\n".join(lines) + ("\n" if lines else "")


def read_jsonl_events(path: str) -> List[Any]:
    return load_events_jsonl(path)


def sum_budget_debits(events: List[Any]) -> float:
    """Cumulative USD spent = sum of budget_debit events."""
    usd = 0.0
    for e in events:
        if e.type == "budget_debit":
            usd += e.usd
    return usd


def parse_ts(ts: Any) -> Optional[datetime]:
    """ISO timestamp -> aware datetime (naive treated as UTC); None on garbage."""
    if not isinstance(ts, str):
        return None
    s = ts
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def sim_days_of(events: List[Any]) -> float:
    """Simulated days elapsed between the first and last event."""
    if len(events) < 2:
        return 0.0
    first = parse_ts(events[0].ts)
    last = parse_ts(events[-1].ts)
    if first is None or last is None:
        return 0.0
    span_ms = (last - first).total_seconds() * 1000.0
    return span_ms / DAY_MS if span_ms > 0 else 0.0


def empty_gate_report() -> GateScriptReport:
    """Replay grades without a live gate-script engine — empty report by contract."""
    return GateScriptReport(neverRaised=[], unexpected=[], resolutions=[])


def harness_error_trial(
    scenario: Scenario, trial_idx: int, err: BaseException, wall_ms: float
) -> TrialResult:
    """A trial that threw inside the harness: status harness_error plus one
    error-status invariant verdict so the failure can never read as green."""
    if err.__traceback__ is not None:
        message = "".join(
            traceback.format_exception(type(err), err, err.__traceback__)
        ).strip()
    else:
        message = "{0}: {1}".format(type(err).__name__, err)
    run_id = "{0}-t{1}-{2}".format(scenario.id, trial_idx, uuid.uuid4().hex[:8])
    verdict = Verdict(
        graderId="harness",
        graderVersion="harness-error-v1",
        class_="invariant",
        scope=VerdictScope(runId=run_id),
        status="error",
        score=0,
        evidence=[
            NoteEvidence(
                kind="note",
                text="trial {0} of {1} threw: {2}".format(trial_idx, scenario.id, message),
            )
        ],
    )
    return TrialResult(
        trialIdx=trial_idx,
        runId=run_id,
        status="harness_error",
        verdicts=[verdict],
        items=[],
        decay=None,
        costUsd=0,
        simDays=0,
        wallMs=wall_ms,
    )


def timestamp_slug(now: Optional[datetime] = None) -> str:
    dt = now or datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def default_out_dir(now: Optional[datetime] = None) -> str:
    return os.path.join("results", timestamp_slug(now))


def events_file_name(scenario_id: str, trial_idx: int) -> str:
    return "events-{0}-t{1}.jsonl".format(scenario_id, trial_idx)


def world_file_name(scenario_id: str, trial_idx: int) -> str:
    return "world-{0}-t{1}.json".format(scenario_id, trial_idx)


def verdicts_file_name(scenario_id: str, trial_idx: int) -> str:
    return "verdicts-{0}-t{1}.json".format(scenario_id, trial_idx)


# ---------------------------------------------------------------------------
# Call-shape adapters for the concurrently-written scoring/graders siblings.
# The TS reference landed with positional signatures plus an equivalent
# single-object form (see RUNNER.NOTES.md); these adapters accept either
# Python port of that shape so the runner keeps working regardless of which
# form the sibling chose. They take the target function as an argument, so
# this module still imports without the siblings present.
# ---------------------------------------------------------------------------


def _sig_params(fn: Callable[..., Any]) -> Optional[Any]:
    try:
        return inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return None


def call_grade_trial(grade_trial_fn: Callable[..., Any], record: Any, judge_mode: Optional[str]) -> Any:
    """gradeTrial(record, {judgeMode}) — returns the (possibly awaitable) result."""
    if judge_mode is None:
        return grade_trial_fn(record)
    params = _sig_params(grade_trial_fn)
    if params is not None:
        if "judge_mode" in params:
            return grade_trial_fn(record, judge_mode=judge_mode)
        if "judgeMode" in params:
            return grade_trial_fn(record, judgeMode=judge_mode)
        if "opts" in params:
            return grade_trial_fn(record, opts={"judgeMode": judge_mode})
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return grade_trial_fn(record, judge_mode=judge_mode)
    return grade_trial_fn(record, {"judgeMode": judge_mode})


def call_build_trial_result(
    fn: Callable[..., Any],
    trial_idx: int,
    run_id: str,
    report: Any,
    verdicts: List[Any],
    scenario: Scenario,
    cost_usd: float,
    sim_days: float,
    wall_ms: float,
) -> TrialResult:
    """build_trial_result — accepts either the scenario-first Python signature
    (scenario, trial_idx, run_id, verdicts, cost_usd, sim_days, wall_ms,
    report=...), the TS positional order (trialIdx, runId, report, verdicts,
    scenario, ...), or a single-object form."""
    params = _sig_params(fn)
    if params is not None:
        names = list(params.keys())
        if len(names) == 1:
            return fn(
                {
                    "scenario": scenario,
                    "trialIdx": trial_idx,
                    "runId": run_id,
                    "report": report,
                    "verdicts": verdicts,
                    "costUsd": cost_usd,
                    "simDays": sim_days,
                    "wallMs": wall_ms,
                }
            )
        if names and names[0] == "scenario":
            return fn(
                scenario, trial_idx, run_id, verdicts, cost_usd, sim_days, wall_ms, report=report
            )
    return fn(trial_idx, run_id, report, verdicts, scenario, cost_usd, sim_days, wall_ms)


def call_build_scenario_verdict(
    fn: Callable[..., Any],
    scenario: Scenario,
    trials: List[TrialResult],
    config: EvalSetConfig,
    quarantined: bool = False,
) -> Any:
    """buildScenarioVerdict(scenario, trials, config, opts?: {quarantined})."""
    params = _sig_params(fn)
    if params is not None and len(params) == 1:
        return fn(
            {"scenario": scenario, "trials": trials, "config": config, "quarantined": quarantined}
        )
    if params is not None and "quarantined" in params:
        return fn(scenario, trials, config, quarantined=quarantined)
    if quarantined:
        return fn(scenario, trials, config, {"quarantined": True})
    return fn(scenario, trials, config)


def call_build_suite_result(
    fn: Callable[..., Any],
    config: EvalSetConfig,
    subject: Dict[str, Any],
    started_at: str,
    finished_at: str,
    scenarios: List[Any],
) -> Any:
    """buildSuiteResult({config, subject, startedAt, finishedAt, scenarios})."""
    params = _sig_params(fn)
    if params is not None and "started_at" in params:
        return fn(
            config=config,
            subject=subject,
            started_at=started_at,
            finished_at=finished_at,
            scenarios=scenarios,
        )
    if params is not None and "startedAt" in params:
        return fn(
            config=config,
            subject=subject,
            startedAt=started_at,
            finishedAt=finished_at,
            scenarios=scenarios,
        )
    return fn(
        {
            "config": config,
            "subject": subject,
            "startedAt": started_at,
            "finishedAt": finished_at,
            "scenarios": scenarios,
        }
    )


def ensure_suite_result(result: Any) -> SuiteResult:
    """Tolerate a sibling build_suite_result that returns a plain dict."""
    if isinstance(result, SuiteResult):
        return result
    return SuiteResult.model_validate(result)
