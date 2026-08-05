"""FastAPI service layer for the Convoy eval harness.

Exposes the corpus (scenarios, eval sets), suite runs (background execution
over the runner), replay, and the HTML reports to the future website/ and to
the runtime side. Run it with:

    uvicorn convoy_evals.api.app:app

`create_app(base_dir=None)` builds an app rooted at an agent-evals checkout
(default: this repo — the parent of the convoy_evals package); scenarios are
read from <base_dir>/scenarios and run artifacts are written under
<base_dir>/results/api/<runId>.

Sibling components (runner / reports / executors) are authored concurrently;
all imports of their exports are lazy, inside route handlers — this module
must import and serve /health, /scenarios, and /sets before they land.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import APIRouter, FastAPI, HTTPException, Request

from . import runs as runs_module
from .models import (
    EvalSetInfo,
    HealthResponse,
    ScenarioSummary,
    SubjectsResponse,
    api_version,
    default_base_dir,
    find_scenario_file,
    iter_scenario_files,
    list_set_files,
    load_scenario_checked,
    read_json,
    resolve_set_path,
    scenario_summary_from_raw,
    set_info,
)

corpus_router = APIRouter()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@corpus_router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", version=api_version())


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@corpus_router.get("/scenarios", response_model=List[ScenarioSummary])
def list_scenarios(request: Request) -> List[ScenarioSummary]:
    base_dir: Path = request.app.state.base_dir
    summaries: List[ScenarioSummary] = []
    for path in iter_scenario_files(base_dir):
        try:
            raw = read_json(path)
        except RuntimeError:
            continue  # unreadable corpus file; GET /scenarios/{id} reports it loudly
        if isinstance(raw, dict):
            summaries.append(scenario_summary_from_raw(raw))
    return sorted(summaries, key=lambda s: s.id)


@corpus_router.get("/scenarios/{scenario_id}")
def get_scenario(scenario_id: str, request: Request) -> Dict[str, Any]:
    base_dir: Path = request.app.state.base_dir
    path = find_scenario_file(base_dir, scenario_id)
    if path is None:
        raise HTTPException(
            status_code=404, detail="unknown scenario: {}".format(scenario_id)
        )
    try:
        load_scenario_checked(path)  # validation gate; the JSON itself is the payload
        raw = read_json(path)
    except Exception as err:
        raise HTTPException(
            status_code=500,
            detail="scenario {} failed to load: {}".format(scenario_id, err),
        )
    return raw


# ---------------------------------------------------------------------------
# Eval sets
# ---------------------------------------------------------------------------


@corpus_router.get("/sets", response_model=List[EvalSetInfo])
def list_sets(request: Request) -> List[EvalSetInfo]:
    base_dir: Path = request.app.state.base_dir
    infos: List[EvalSetInfo] = []
    for path in list_set_files(base_dir):
        try:
            infos.append(set_info(path))
        except Exception:
            continue  # invalid set file; GET /sets/{name} reports it loudly
    return infos


@corpus_router.get("/sets/{set_name}", response_model=EvalSetInfo)
def get_set(set_name: str, request: Request) -> EvalSetInfo:
    base_dir: Path = request.app.state.base_dir
    path = resolve_set_path(base_dir, set_name)
    if path is None:
        raise HTTPException(status_code=404, detail="unknown eval set: {}".format(set_name))
    try:
        return set_info(path)
    except Exception as err:
        raise HTTPException(
            status_code=500, detail="eval set {} failed to load: {}".format(set_name, err)
        )


# ---------------------------------------------------------------------------
# Subjects
# ---------------------------------------------------------------------------

_RUNTIME_NOTE = (
    "runtime:* subjects (the real agent runtime) attach in-process via the "
    "RuntimeFactory seam and are not startable through this API yet."
)


@corpus_router.get("/subjects", response_model=SubjectsResponse)
def list_subjects() -> SubjectsResponse:
    executors = runs_module.try_load_executors()
    if executors is None:
        return SubjectsResponse(
            subjects=[],
            note="executor registry (convoy_evals.executors.EXECUTORS) has not "
            "landed yet. " + _RUNTIME_NOTE,
        )
    return SubjectsResponse(
        subjects=["scripted:{}".format(name) for name in sorted(executors)],
        note=_RUNTIME_NOTE,
    )


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------


def create_app(base_dir: Optional[Union[str, Path]] = None) -> FastAPI:
    """Build the harness API app rooted at an agent-evals checkout."""
    resolved = Path(base_dir).resolve() if base_dir is not None else default_base_dir()
    application = FastAPI(
        title="Convoy eval harness API",
        description="Suite runs, replay grading, corpus browsing, and reports "
        "for the Convoy eval harness.",
        version=api_version(),
    )
    application.state.base_dir = resolved
    application.include_router(corpus_router)
    application.include_router(runs_module.router)
    return application


app = create_app()
