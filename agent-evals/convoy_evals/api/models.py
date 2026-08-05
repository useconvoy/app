"""API models + corpus access helpers for the FastAPI service layer.

Request/response models are pydantic v2 with camelCase field names, matching
the project-wide JSON convention (the scenario/verdict schemas are camelCase).

The corpus helpers prefer the runner's store (``convoy_evals.runner.store``)
when it has landed and fall back to direct schema validation otherwise, so the
listing endpoints keep working while sibling components are still being
written concurrently. Never import sibling modules at module level here —
lazy, inside functions, always.
"""

from __future__ import annotations

import inspect
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def default_base_dir() -> Path:
    """The agent-evals repo dir (parent of the convoy_evals package)."""
    return Path(__file__).resolve().parents[2]


def scenarios_dir(base_dir: Path) -> Path:
    return Path(base_dir) / "scenarios"


def sets_dir(base_dir: Path) -> Path:
    return scenarios_dir(base_dir) / "sets"


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

_FALLBACK_VERSION = "0.1.0"


def api_version() -> str:
    try:
        import importlib.metadata as importlib_metadata

        return importlib_metadata.version("convoy-evals")
    except Exception:
        return _FALLBACK_VERSION


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: str
    version: str


class ScenarioSummary(BaseModel):
    id: str
    title: str
    kind: str
    missionType: str
    tags: List[str] = Field(default_factory=list)
    items: int = 0


class EvalSetInfo(BaseModel):
    """One eval-set config; `id` is the file stem the API addresses it by."""

    id: str
    name: str
    version: str
    scenarios: List[str]
    config: Dict[str, Any]


class SuiteRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evalSet: str
    subject: str
    filter: Optional[List[str]] = None
    usdCap: Optional[float] = Field(default=None, gt=0)


class SuiteRunAccepted(BaseModel):
    runId: str
    status: str = "running"


class RunSummary(BaseModel):
    runId: str
    status: str  # 'running' | 'completed' | 'failed'
    startedAt: str
    subject: str
    evalSet: str
    green: Optional[bool] = None


class RunDetail(RunSummary):
    finishedAt: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Dict[str, Any]] = None


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evalSet: str
    recordsDir: str


class SubjectsResponse(BaseModel):
    subjects: List[str]
    note: str


# ---------------------------------------------------------------------------
# JSON reading with pathful errors
# ---------------------------------------------------------------------------


def read_json(path: Path) -> Any:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as err:
        raise RuntimeError("cannot read {}: {}".format(path, err))
    try:
        return json.loads(text)
    except ValueError as err:
        raise RuntimeError("invalid JSON in {}: {}".format(path, err))


# ---------------------------------------------------------------------------
# Scenario corpus access
# ---------------------------------------------------------------------------

_SCENARIO_SUFFIX = ".scenario.json"


def iter_scenario_files(base_dir: Path) -> List[Path]:
    sdir = scenarios_dir(base_dir)
    if not sdir.is_dir():
        return []
    return sorted(p for p in sdir.iterdir() if p.name.endswith(_SCENARIO_SUFFIX))


def scenario_summary_from_raw(raw: Dict[str, Any]) -> ScenarioSummary:
    return ScenarioSummary(
        id=str(raw.get("id", "")),
        title=str(raw.get("title", "")),
        kind=str(raw.get("kind", "")),
        missionType=str(raw.get("missionType", "")),
        tags=list(raw.get("tags") or []),
        items=len(raw.get("items") or []),
    )


def find_scenario_file(base_dir: Path, scenario_id: str) -> Optional[Path]:
    """Filename convention first (<id>.scenario.json), then a content scan."""
    candidate = scenarios_dir(base_dir) / (scenario_id + _SCENARIO_SUFFIX)
    if candidate.is_file():
        return candidate
    for path in iter_scenario_files(base_dir):
        try:
            raw = read_json(path)
        except RuntimeError:
            continue
        if isinstance(raw, dict) and raw.get("id") == scenario_id:
            return path
    return None


def load_scenario_checked(path: Path) -> Any:
    """Load + validate one scenario. Prefers the runner store's loader (it is
    the component that owns corpus loading); falls back to the frozen schema
    while that sibling is still landing."""
    try:
        from convoy_evals.runner.store import load_scenario  # type: ignore
    except Exception:
        load_scenario = None
    if load_scenario is not None:
        return load_scenario(str(path))
    from convoy_evals.schema.scenario import Scenario

    return Scenario.model_validate(read_json(path))


# ---------------------------------------------------------------------------
# Eval-set corpus access
# ---------------------------------------------------------------------------


def list_set_files(base_dir: Path) -> List[Path]:
    d = sets_dir(base_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.suffix == ".json")


def resolve_set_path(base_dir: Path, name: str) -> Optional[Path]:
    """Resolve an eval-set name to its file: file stem first, then the config's
    declared `name` field."""
    candidate = sets_dir(base_dir) / (name + ".json")
    if candidate.is_file():
        return candidate
    for path in list_set_files(base_dir):
        try:
            raw = read_json(path)
        except RuntimeError:
            continue
        if isinstance(raw, dict) and raw.get("name") == name:
            return path
    return None


def load_set_config(path: Path) -> Any:
    """Load + validate an eval-set config. Prefers the runner store's
    load_eval_set (whatever its loaded shape, we only need `.config`); falls
    back to direct schema validation."""
    try:
        from convoy_evals.runner.store import load_eval_set  # type: ignore
    except Exception:
        load_eval_set = None
    if load_eval_set is not None:
        loaded = load_eval_set(str(path))
        config = getattr(loaded, "config", None)
        if config is None and isinstance(loaded, dict):
            config = loaded.get("config", loaded)
        if config is None:
            config = loaded
        if config is not None and not isinstance(config, (str, bytes)):
            return config
    from convoy_evals.schema.scenario import EvalSetConfig

    return EvalSetConfig.model_validate(read_json(path))


def set_info(path: Path) -> EvalSetInfo:
    raw = read_json(path)
    if not isinstance(raw, dict):
        raise RuntimeError("eval set {} is not a JSON object".format(path))
    # Validate through the frozen schema so a malformed set errors loudly.
    from convoy_evals.schema.scenario import EvalSetConfig

    config = EvalSetConfig.model_validate(raw)
    return EvalSetInfo(
        id=path.stem,
        name=config.name,
        version=config.version,
        scenarios=list(config.scenarios),
        config=raw,
    )


# ---------------------------------------------------------------------------
# Sibling-call adaptation
# ---------------------------------------------------------------------------


def to_camel(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def call_adaptive(fn: Any, args: List[Any], values: Dict[str, Any]) -> Any:
    """Call a concurrently-authored sibling function with best-effort kwargs.

    `values` is keyed by canonical snake_case names; None values are dropped.
    Strategy, in order:
      - uninspectable signature or **kwargs -> pass all snake_case values;
      - otherwise pass the intersection with the signature, trying the
        camelCase alias of each name too (the schemas are camelCase, so a
        sibling may have mirrored that in its options);
      - if nothing matched but the function has exactly one remaining required
        positional parameter, assume a TS-style single options argument and
        pass the values as a dict, retrying once with a SimpleNamespace if
        attribute access was expected. (The retry can only follow an
        immediate TypeError/AttributeError/KeyError — acceptable for the
        renderers and the runner, which read their options up front.)
    """
    filtered = {k: v for k, v in values.items() if v is not None}
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return fn(*args, **filtered)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **filtered)
    accepted: Dict[str, Any] = {}
    for key, val in filtered.items():
        if key in params:
            accepted[key] = val
        elif to_camel(key) in params:
            accepted[to_camel(key)] = val
    if accepted or not filtered:
        return fn(*args, **accepted)
    positional = [
        p
        for p in params.values()
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        and p.default is inspect.Parameter.empty
    ]
    if len(positional) - len(args) == 1:
        try:
            return fn(*(list(args) + [dict(filtered)]))
        except (TypeError, AttributeError, KeyError):
            return fn(*(list(args) + [SimpleNamespace(**filtered)]))
    return fn(*args, **accepted)


def dump_jsonable(value: Any) -> Optional[Dict[str, Any]]:
    """Best-effort conversion of a sibling-produced result to a JSON dict."""
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(by_alias=True, mode="json")
    if isinstance(value, dict):
        return value
    dump = getattr(value, "model_dump", None)  # pydantic model from another import path
    if callable(dump):
        try:
            return dump(by_alias=True, mode="json")
        except TypeError:
            return dump()
    if is_dataclass(value):
        return asdict(value)
    return {"repr": repr(value)}


def green_of(result: Any) -> Optional[bool]:
    if result is None:
        return None
    green = getattr(result, "green", None)
    if green is None and isinstance(result, dict):
        green = result.get("green")
    if isinstance(green, bool):
        return green
    return None
