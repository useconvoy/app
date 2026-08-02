"""Suite-run registry + the /suite-runs and /replay routes.

The run registry is a module-level dict guarded by a Lock — deliberately v1:
process-local, lost on restart, single-worker only. When the website needs
durable, multi-node run history this dict is replaced by a Postgres `runs`
table (runId primary key, same fields) written by the runner; the route
shapes were chosen so that swap touches only this module.

Sibling components (runner / reports / executors) are being written
concurrently, so every one of their imports is lazy — inside handlers or the
background job — and adaptive (`call_adaptive`): the API module must import
and serve its corpus endpoints even before the siblings land. A background
run whose sibling import or execution fails is recorded as status 'failed'
with the error string and surfaces via GET /suite-runs/{id} — never a silent
loss, never a crashed server.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from .models import (
    ReplayRequest,
    RunDetail,
    RunSummary,
    SuiteRunAccepted,
    SuiteRunRequest,
    call_adaptive,
    dump_jsonable,
    find_scenario_file,
    green_of,
    load_scenario_checked,
    load_set_config,
    read_json,
    resolve_set_path,
)

router = APIRouter()

# ---------------------------------------------------------------------------
# Registry (v1: in-memory; Postgres replaces it — see module docstring)
# ---------------------------------------------------------------------------

RUNS: Dict[str, Dict[str, Any]] = {}
_RUNS_LOCK = threading.Lock()


def reset_registry() -> None:
    """Test helper: forget every run."""
    with _RUNS_LOCK:
        RUNS.clear()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_run_id() -> str:
    return "{}-{}".format(
        time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()), uuid.uuid4().hex[:8]
    )


def _get_run_or_404(run_id: str) -> Dict[str, Any]:
    with _RUNS_LOCK:
        run = RUNS.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="unknown run id: {}".format(run_id))
        return dict(run)


def _summary(run: Dict[str, Any]) -> RunSummary:
    return RunSummary(
        runId=run["runId"],
        status=run["status"],
        startedAt=run["startedAt"],
        subject=run["subject"],
        evalSet=run["evalSet"],
        green=green_of(run.get("result")),
    )


# ---------------------------------------------------------------------------
# Subject validation
# ---------------------------------------------------------------------------


def try_load_executors() -> Optional[Dict[str, Any]]:
    """The executors registry, or None while that sibling hasn't landed."""
    try:
        from convoy_evals.executors import EXECUTORS  # type: ignore

        return dict(EXECUTORS)
    except Exception:
        return None


def validate_subject_or_422(subject: str) -> None:
    if subject.startswith("runtime:"):
        raise HTTPException(
            status_code=422,
            detail=(
                "runtime:* subjects are not startable through this API yet — the real "
                "runtime attaches in-process via the RuntimeFactory seam. Use a "
                "'scripted:<executor>' subject (see GET /subjects)."
            ),
        )
    if not subject.startswith("scripted:") or not subject.split(":", 1)[1]:
        raise HTTPException(
            status_code=422,
            detail=(
                "unknown subject {!r}: expected 'scripted:<executor>' "
                "(see GET /subjects)".format(subject)
            ),
        )
    name = subject.split(":", 1)[1]
    executors = try_load_executors()
    if executors is not None and name not in executors:
        raise HTTPException(
            status_code=422,
            detail="unknown scripted executor {!r} (available: {})".format(
                name, ", ".join(sorted(executors))
            ),
        )


# ---------------------------------------------------------------------------
# Background execution
# ---------------------------------------------------------------------------


def _resolve_maybe_awaitable(value: Any) -> Any:
    """Sibling entry points may be sync or async; drive coroutines to a result.

    This runs inside a worker thread (sync background task), so a private
    event loop via asyncio.run is safe.
    """
    if inspect.isawaitable(value):

        async def _drive(aw: Any) -> Any:
            return await aw

        return asyncio.run(_drive(value))
    return value


def _subject_arg(subject: str) -> Any:
    """The API boundary speaks CLI-style subject strings ('scripted:golden');
    the runner takes the structured shape {'kind': 'scripted', 'executor': ...}.
    Convert here — runtime subjects would need a live RuntimeFactory and are
    rejected at validation before this point."""
    if subject.startswith("scripted:"):
        return {"kind": "scripted", "executor": subject.split(":", 1)[1]}
    return subject


def execute_suite_run(
    run_id: str,
    eval_set_path: str,
    subject: str,
    scenario_filter: Optional[List[str]],
    usd_cap: Optional[float],
    out_dir: str,
) -> None:
    """The background job behind POST /suite-runs. Sync on purpose: Starlette
    runs sync background tasks in the threadpool, so a long suite never blocks
    the event loop (run_suite is async; it gets its own private loop here)."""
    try:
        from convoy_evals.runner.suite_runner import run_suite  # lazy: sibling

        result = call_adaptive(
            run_suite,
            [],
            {
                "eval_set_path": eval_set_path,
                "subject": _subject_arg(subject),
                "scenario_filter": scenario_filter,
                "usd_cap": usd_cap,
                "out_dir": out_dir,
            },
        )
        result = _resolve_maybe_awaitable(result)
        with _RUNS_LOCK:
            run = RUNS.get(run_id)
            if run is not None:
                run["status"] = "completed"
                run["result"] = result
                run["finishedAt"] = _now_iso()
    except Exception as err:  # a failed run is data, not a crash
        with _RUNS_LOCK:
            run = RUNS.get(run_id)
            if run is not None:
                run["status"] = "failed"
                run["error"] = "{}: {}".format(type(err).__name__, err)
                run["finishedAt"] = _now_iso()


# ---------------------------------------------------------------------------
# Routes: suite runs
# ---------------------------------------------------------------------------


@router.post("/suite-runs", status_code=202, response_model=SuiteRunAccepted)
def create_suite_run(
    body: SuiteRunRequest, background: BackgroundTasks, request: Request
) -> SuiteRunAccepted:
    base_dir: Path = request.app.state.base_dir
    set_path = resolve_set_path(base_dir, body.evalSet)
    if set_path is None:
        raise HTTPException(
            status_code=404, detail="unknown eval set: {}".format(body.evalSet)
        )
    validate_subject_or_422(body.subject)
    if body.filter:
        try:
            config = load_set_config(set_path)
            set_scenarios = list(getattr(config, "scenarios", []) or [])
        except Exception as err:
            raise HTTPException(
                status_code=500, detail="eval set {} failed to load: {}".format(body.evalSet, err)
            )
        unknown = [sid for sid in body.filter if sid not in set_scenarios]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail="filter scenario(s) {} not in eval set {} (scenarios: {})".format(
                    ", ".join(unknown), body.evalSet, ", ".join(set_scenarios)
                ),
            )
    run_id = new_run_id()
    out_dir = Path(base_dir) / "results" / "api" / run_id
    with _RUNS_LOCK:
        RUNS[run_id] = {
            "runId": run_id,
            "status": "running",
            "startedAt": _now_iso(),
            "subject": body.subject,
            "evalSet": body.evalSet,
            "outDir": str(out_dir),
            "result": None,
            "error": None,
            "finishedAt": None,
        }
    background.add_task(
        execute_suite_run,
        run_id,
        str(set_path),
        body.subject,
        list(body.filter) if body.filter else None,
        body.usdCap,
        str(out_dir),
    )
    return SuiteRunAccepted(runId=run_id)


@router.get("/suite-runs", response_model=List[RunSummary])
def list_suite_runs() -> List[RunSummary]:
    with _RUNS_LOCK:
        runs = [dict(r) for r in RUNS.values()]
    return [_summary(r) for r in runs]


@router.get("/suite-runs/{run_id}", response_model=RunDetail)
def get_suite_run(run_id: str) -> RunDetail:
    run = _get_run_or_404(run_id)
    return RunDetail(
        runId=run["runId"],
        status=run["status"],
        startedAt=run["startedAt"],
        subject=run["subject"],
        evalSet=run["evalSet"],
        green=green_of(run.get("result")),
        finishedAt=run.get("finishedAt"),
        error=run.get("error"),
        result=dump_jsonable(run.get("result")),
    )


def _completed_run_or_409(run_id: str) -> Dict[str, Any]:
    run = _get_run_or_404(run_id)
    if run["status"] == "running":
        raise HTTPException(
            status_code=409, detail="run {} is still running".format(run_id)
        )
    if run["status"] != "completed" or run.get("result") is None:
        raise HTTPException(
            status_code=409,
            detail="run {} failed; no result to report on ({})".format(
                run_id, run.get("error") or "no error recorded"
            ),
        )
    return run


def _item_floor_of(request: Request, eval_set: str) -> Optional[float]:
    """thresholds.itemFloor from the run's eval set — best-effort, for the
    suite report's Q(n) chart threshold line."""
    try:
        set_path = resolve_set_path(request.app.state.base_dir, eval_set)
        if set_path is None:
            return None
        config = load_set_config(set_path)
        thresholds = getattr(config, "thresholds", None)
        if thresholds is None and isinstance(config, dict):
            thresholds = config.get("thresholds")
        floor = getattr(thresholds, "itemFloor", None)
        if floor is None and isinstance(thresholds, dict):
            floor = thresholds.get("itemFloor")
        return float(floor) if floor is not None else None
    except Exception:
        return None


@router.get("/suite-runs/{run_id}/report", response_class=HTMLResponse)
def get_suite_run_report(run_id: str, request: Request) -> HTMLResponse:
    run = _completed_run_or_409(run_id)
    try:
        from convoy_evals.reports.suite_report import render_suite_report  # lazy: sibling
    except Exception as err:
        raise HTTPException(
            status_code=503,
            detail="suite report renderer not available yet "
            "(convoy_evals.reports.suite_report): {}".format(err),
        )
    result = run["result"]
    try:
        html = call_adaptive(
            render_suite_report,
            [result],
            {"item_floor": _item_floor_of(request, run["evalSet"])},
        )
        html = _resolve_maybe_awaitable(html)
    except Exception as err:
        raise HTTPException(
            status_code=500, detail="suite report rendering failed: {}".format(err)
        )
    return HTMLResponse(content=str(html))


# ---------------------------------------------------------------------------
# Routes: rehearsal report from stored artifacts
# ---------------------------------------------------------------------------


def _attr_view(value: Any) -> Any:
    """Recursive attribute view over a raw JSON value, so renderers written
    against event models (`e.type`, `e.gateId`) still work for lines that
    fail strict contract validation."""
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _attr_view(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_attr_view(v) for v in value]
    return value


def _load_events(path: Path) -> List[Any]:
    """Parse an events JSONL artifact into event models. Lines that fail the
    frozen contract's validation (recorded artifacts are sibling-produced;
    the report should render, not 500) fall back to an attribute view."""
    from convoy_evals.runtime.events import parse_event  # frozen contract

    events: List[Any] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        raw = json.loads(line)
        try:
            events.append(parse_event(raw))
        except Exception:
            events.append(_attr_view(raw))
    return events


def _find_trial(result: Any, scenario_id: str, trial_idx: int) -> Any:
    scenarios = getattr(result, "scenarios", None)
    if scenarios is None and isinstance(result, dict):
        scenarios = result.get("scenarios")
    for sv in scenarios or []:
        sid = getattr(sv, "scenarioId", None)
        if sid is None and isinstance(sv, dict):
            sid = sv.get("scenarioId")
        if sid != scenario_id:
            continue
        trials = getattr(sv, "trials", None)
        if trials is None and isinstance(sv, dict):
            trials = sv.get("trials")
        for trial in trials or []:
            idx = getattr(trial, "trialIdx", None)
            if idx is None and isinstance(trial, dict):
                idx = trial.get("trialIdx")
            if idx == trial_idx:
                return trial
        raise HTTPException(
            status_code=404,
            detail="scenario {} has no trial {} in this run".format(scenario_id, trial_idx),
        )
    raise HTTPException(
        status_code=404,
        detail="scenario {} is not part of this run's result".format(scenario_id),
    )


@router.get(
    "/suite-runs/{run_id}/rehearsal/{scenario_id}", response_class=HTMLResponse
)
def get_rehearsal_report(
    run_id: str,
    scenario_id: str,
    request: Request,
    trial: int = Query(default=0, ge=0),
) -> HTMLResponse:
    run = _completed_run_or_409(run_id)
    trial_result = _find_trial(run["result"], scenario_id, trial)

    out_dir = Path(run["outDir"])
    events_path = out_dir / "events-{}-t{}.jsonl".format(scenario_id, trial)
    world_path = out_dir / "world-{}-t{}.json".format(scenario_id, trial)
    for path in (events_path, world_path):
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="recorded artifact missing for {} trial {}: {}".format(
                    scenario_id, trial, path.name
                ),
            )

    scenario_path = find_scenario_file(request.app.state.base_dir, scenario_id)
    if scenario_path is None:
        raise HTTPException(
            status_code=404,
            detail="scenario {} has no *.scenario.json in the corpus".format(scenario_id),
        )

    try:
        from convoy_evals.reports.rehearsal_report import (  # lazy: sibling
            render_rehearsal_report,
        )
    except Exception as err:
        raise HTTPException(
            status_code=503,
            detail="rehearsal report renderer not available yet "
            "(convoy_evals.reports.rehearsal_report): {}".format(err),
        )
    try:
        from convoy_evals.sandbox.api import WorldBundle

        scenario = load_scenario_checked(scenario_path)
        events = _load_events(events_path)
        world = WorldBundle.from_json(read_json(world_path))
        # "world" and "world_bundle" are aliases for the same value: the
        # landed renderer names the parameter world_bundle; call_adaptive
        # keeps whichever the signature declares.
        html = call_adaptive(
            render_rehearsal_report,
            [],
            {
                "scenario": scenario,
                "trial": trial_result,
                "events": events,
                "world": world,
                "world_bundle": world,
            },
        )
        html = _resolve_maybe_awaitable(html)
    except HTTPException:
        raise
    except Exception as err:
        raise HTTPException(
            status_code=500, detail="rehearsal report rendering failed: {}".format(err)
        )
    return HTMLResponse(content=str(html))


# ---------------------------------------------------------------------------
# Routes: replay (synchronous — cheap and deterministic by design)
# ---------------------------------------------------------------------------


@router.post("/replay")
def replay(body: ReplayRequest, request: Request) -> Dict[str, Any]:
    base_dir: Path = request.app.state.base_dir
    set_path = resolve_set_path(base_dir, body.evalSet)
    if set_path is None:
        raise HTTPException(
            status_code=404, detail="unknown eval set: {}".format(body.evalSet)
        )
    records_dir = Path(body.recordsDir)
    if not records_dir.is_absolute():
        records_dir = Path(base_dir) / records_dir
    if not records_dir.is_dir():
        raise HTTPException(
            status_code=404, detail="records directory not found: {}".format(records_dir)
        )
    try:
        from convoy_evals.runner.replay import replay_suite  # lazy: sibling
    except Exception as err:
        raise HTTPException(
            status_code=503,
            detail="replay runner not available yet "
            "(convoy_evals.runner.replay): {}".format(err),
        )
    try:
        result = call_adaptive(
            replay_suite,
            [],
            {"eval_set_path": str(set_path), "records_dir": str(records_dir)},
        )
        result = _resolve_maybe_awaitable(result)
    except Exception as err:
        raise HTTPException(status_code=500, detail="replay failed: {}".format(err))
    dumped = dump_jsonable(result)
    return dumped if dumped is not None else {}
