"""Shared grader plumbing: canonical hashing (graderVersion), answer-key
resolution (KeyRef grammar incl. per-item scope), event ordering, and the
EventMatcher/GateMatcher predicates used by trajectory + probe graders.

Port of src/graders/util.ts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel

from ..sandbox.api import WorldMessage
from ..schema.match import is_key_ref, key_ref_path, matches, resolve_path
from ..schema.scenario import AnswerKey, EventMatcher, GateMatcher
from ..schema.verdict import (
    ArtifactEvidence,
    EventEvidence,
    NoteEvidence,
    WorldEvidence,
)

# ---------------------------------------------------------------------------
# Canonical JSON + hashing — graderVersion = sha256(canonicalJson(spec))
# ---------------------------------------------------------------------------


def _sort_value(v: Any) -> Any:
    if isinstance(v, BaseModel):
        # Pydantic models: dump by alias with None (undefined-equivalent) dropped.
        v = v.model_dump(by_alias=True, exclude_none=True)
    if isinstance(v, list):
        return [_sort_value(x) for x in v]
    if isinstance(v, dict):
        # Keys sorted recursively; None kept (it is JSON null, TS drops only undefined).
        return {k: _sort_value(v[k]) for k in sorted(v.keys())}
    if isinstance(v, float) and not isinstance(v, bool) and v == int(v):
        # JS JSON.stringify(1.0) === "1" — keep hashes stable across harnesses.
        return int(v)
    return v


def canonical_json(value: Any) -> str:
    """Deterministic JSON: object keys sorted recursively, compact separators."""
    return json.dumps(_sort_value(value), separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def grader_version_of(spec: Any) -> str:
    """Content hash of a grader spec — stamped on every Verdict it emits."""
    return sha256_hex(canonical_json(spec))


# ---------------------------------------------------------------------------
# Item scope
# ---------------------------------------------------------------------------


@dataclass
class ItemCtx:
    """The two names an item carries: `itemId` is the domain key events/tags use
    (event.itemRef == itemId; '{itemRef}' interpolation); `keyRef` selects the
    answerKey.perItem subtree ('item.X' KeyRefs resolve through it)."""

    itemId: str
    keyRef: str


KeyResolver = Callable[[str], Any]


def as_answer_key(key: Any) -> AnswerKey:
    if isinstance(key, AnswerKey):
        return key
    return AnswerKey.model_validate(key)


def _key_root(key: Any) -> Dict[str, Any]:
    if isinstance(key, BaseModel):
        return key.model_dump(by_alias=True, exclude_none=True)
    if isinstance(key, dict):
        return key
    return as_answer_key(key).model_dump(by_alias=True, exclude_none=True)


def make_key_resolver(key: Any, item_ctx: Optional[ItemCtx]) -> KeyResolver:
    """KeyRef grammar: 'facts.X' and 'perItem.<id>.X' resolve against the key
    root; 'item.X' resolves against perItem[currentItem] and is only legal in
    item scope. Single match unwraps; no match -> None; multi-match -> list."""
    root = _key_root(key)

    def resolver(path: str) -> Any:
        target: Any = root
        rest = path
        if path == "item" or path.startswith("item."):
            if item_ctx is None:
                raise ValueError('KeyRef "%s" uses item.* outside item scope' % path)
            per_item = root.get("perItem") or {}
            target = per_item.get(item_ctx.keyRef, {})
            rest = "" if path == "item" else path[len("item.") :]
        values = resolve_path(target, rest)
        if len(values) == 0:
            return None
        return values[0] if len(values) == 1 else values

    return resolver


# ---------------------------------------------------------------------------
# Event ordering + scoping
# ---------------------------------------------------------------------------


def order_events(events: List[Any]) -> List[Any]:
    """Order by (ts, seq) — zero-duration sim ties resolve by append order."""
    return sorted(events, key=lambda e: (e.ts, e.seq))


def scope_events(events: List[Any], item_ctx: Optional[ItemCtx]) -> List[Any]:
    """Item scope: an event belongs to an item iff event.itemRef == item domain key."""
    ordered = order_events(events)
    if item_ctx is None:
        return ordered
    return [e for e in ordered if e.itemRef == item_ctx.itemId]


def event_dict(e: Any) -> Dict[str, Any]:
    """Plain-dict view of a pydantic event model for path-based matching."""
    if isinstance(e, BaseModel):
        return e.model_dump(exclude_none=True)
    return dict(e)


def parse_ts_ms(ts: str) -> float:
    """ISO timestamp -> epoch milliseconds (accepts trailing 'Z')."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000.0


def message_to_dict(m: WorldMessage) -> Dict[str, Any]:
    """WorldMessage dataclass -> wire-shaped dict (with a 'from' key) for matchers."""
    d = asdict(m)
    d["from"] = d.pop("from_")
    return d


# ---------------------------------------------------------------------------
# Matchers
# ---------------------------------------------------------------------------


def match_event(m: EventMatcher, e: Any, resolve_key: Optional[KeyResolver] = None) -> bool:
    """ArgsMatcher inside an EventMatcher runs against the event's tool-call
    `args` payload when the event carries one (tool_call / tool_intent /
    tool_executed / tool_denied) — scenario authors write paths like "to", not
    "args.to". Events without an `args` field fall back to matching the whole
    event dict."""
    ed = event_dict(e)
    if m.type is not None:
        types = m.type if isinstance(m.type, list) else [m.type]
        if ed.get("type") not in types:
            return False
    if m.tool is not None and ed.get("tool") != m.tool:
        return False
    if m.itemRef is not None and ed.get("itemRef") != m.itemRef:
        return False
    if m.args is not None:
        target = ed["args"] if "args" in ed else ed
        if not matches(m.args, target, resolve_key):
            return False
    return True


def match_gate(g: GateMatcher, e: Any, resolve_key: Optional[KeyResolver] = None) -> bool:
    if g.kind is not None and e.kind != g.kind:
        return False
    if g.stepTag is not None and e.stepTag != g.stepTag:
        return False
    if g.payload is not None and not matches(g.payload, e.payload, resolve_key):
        return False
    return True


# ---------------------------------------------------------------------------
# Evidence + result helpers
# ---------------------------------------------------------------------------


def ev_note(text: str) -> NoteEvidence:
    return NoteEvidence(kind="note", text=text)


def ev_event(event_id: str, note: Optional[str] = None) -> EventEvidence:
    return EventEvidence(kind="event", eventId=event_id, note=note)


def ev_artifact(hash_: str, excerpt: Optional[str] = None) -> ArtifactEvidence:
    return ArtifactEvidence(kind="artifact", hash=hash_, excerpt=excerpt)


def ev_world(query: str, result: Any) -> WorldEvidence:
    return WorldEvidence(kind="world", query=query, result=result)


def clamp01(n: float) -> float:
    if n != n:  # NaN
        return 0.0
    return min(1.0, max(0.0, n))


def fmt_num(n: Any) -> str:
    """Render 2.0 as '2' (JS number formatting parity for evidence notes)."""
    if isinstance(n, bool):
        return str(n)
    if isinstance(n, float) and n == int(n):
        return str(int(n))
    return str(n)


@dataclass
class GraderOutcome:
    """What every grader module returns; __init__.grade_trial wraps it into a Verdict."""

    status: str  # 'pass' | 'fail' | 'error'
    score: float
    evidence: List[Any] = field(default_factory=list)
    lowConfidence: Optional[bool] = None
    advisory: Optional[bool] = None
    costUsd: Optional[float] = None


@dataclass
class AssertResult:
    """Per-assertion result; a grader's score is the mean of its asserts' scores."""

    pass_: bool
    # 0..1 — binary asserts emit 0|1; checklist emits the weighted fraction.
    score: float
    evidence: List[Any] = field(default_factory=list)


def binary(pass_: bool, evidence: List[Any]) -> AssertResult:
    return AssertResult(pass_=pass_, score=1.0 if pass_ else 0.0, evidence=evidence)


def combine_asserts(results: List[AssertResult]) -> GraderOutcome:
    if len(results) == 0:
        return GraderOutcome(status="pass", score=1.0, evidence=[])
    score = clamp01(sum(r.score for r in results) / len(results))
    failed = [r for r in results if not r.pass_]
    if len(failed) == 0:
        # Keep at most one supporting evidence item per assert on pass.
        evidence = [e for r in results for e in r.evidence[:1]]
        return GraderOutcome(status="pass", score=1.0, evidence=evidence)
    evidence = [e for r in failed for e in r.evidence]
    if len(evidence) == 0:
        evidence.append(ev_note("assertion failed (no evidence captured)"))
    return GraderOutcome(status="fail", score=score, evidence=evidence)
