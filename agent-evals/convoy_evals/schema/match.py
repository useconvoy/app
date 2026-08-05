"""Matchers are DATA, not code — JSONPath-lite paths + comparison ops.

Port of src/schema/match.ts. Path grammar: dot segments, numeric indices, `*`
wildcard over arrays/objects. `exists`/`absent` quantify over the path itself;
every other op passes if ANY path-resolved value satisfies it; all clauses of
a matcher must hold.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict

CmpOp = Literal[
    "eq", "neq", "lt", "lte", "gt", "gte", "contains", "regex", "in", "exists", "absent"
]

KeyResolver = Callable[[str], Any]


class KeyRef(BaseModel):
    """Resolves into the sealed answer key at grade time — keys never inline in scenarios."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    key: str  # serialized as {"$key": "..."}

    def __init__(self, **data: Any) -> None:
        if "$key" in data:
            data = {"key": data["$key"]}
        super().__init__(**data)

    def model_dump(self, **kw: Any) -> Any:  # type: ignore[override]
        return {"$key": self.key}


def is_key_ref(v: Any) -> bool:
    return (isinstance(v, dict) and isinstance(v.get("$key"), str)) or isinstance(v, KeyRef)


def key_ref_path(v: Any) -> str:
    return v.key if isinstance(v, KeyRef) else v["$key"]


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    op: CmpOp
    value: Any = None


class ArgsMatcher(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all: List[Clause]


def resolve_path(value: Any, path: str) -> List[Any]:
    """Resolve a dotted path against a value; wildcard fans out. Missing → []."""
    current: List[Any] = [value]
    if path in ("", "$"):
        return current
    for seg in path.split("."):
        nxt: List[Any] = []
        for v in current:
            if v is None or not isinstance(v, (dict, list)):
                continue
            if seg == "*":
                nxt.extend(v if isinstance(v, list) else list(v.values()))
            elif isinstance(v, list):
                try:
                    idx = int(seg)
                except ValueError:
                    continue
                if 0 <= idx < len(v):
                    nxt.append(v[idx])
            elif seg in v:
                nxt.append(v[seg])
        current = nxt
    return current


def _canon(v: Any) -> str:
    return json.dumps(v, sort_keys=True, separators=(",", ":"), default=str)


def compare(op: CmpOp, actual: Any, expected: Any) -> bool:
    # exists/absent quantify over the resolved path list — matches() handles
    # them; a direct compare() call treats them as presence checks.
    if op == "exists":
        return actual is not None
    if op == "absent":
        return actual is None
    if op == "eq":
        return _canon(actual) == _canon(expected)
    if op == "neq":
        return _canon(actual) != _canon(expected)
    if op in ("lt", "lte", "gt", "gte"):
        if not isinstance(actual, (int, float)) or not isinstance(expected, (int, float)):
            return False
        if isinstance(actual, bool) or isinstance(expected, bool):
            return False
        return {
            "lt": actual < expected,
            "lte": actual <= expected,
            "gt": actual > expected,
            "gte": actual >= expected,
        }[op]
    if op == "contains":
        if isinstance(actual, str):
            return isinstance(expected, str) and expected in actual
        if isinstance(actual, list):
            return any(_canon(a) == _canon(expected) for a in actual)
        return False
    if op == "regex":
        return (
            isinstance(actual, str)
            and isinstance(expected, str)
            and re.search(expected, actual) is not None
        )
    if op == "in":
        return isinstance(expected, list) and any(_canon(e) == _canon(actual) for e in expected)
    raise ValueError(f"unknown op: {op}")


def _missing_key_resolver(k: str) -> Any:
    raise RuntimeError(f'KeyRef "{k}" used but no answer key resolver provided')


def matches(
    matcher: Union[ArgsMatcher, dict],
    value: Any,
    resolve_key: Optional[KeyResolver] = None,
) -> bool:
    m = matcher if isinstance(matcher, ArgsMatcher) else ArgsMatcher.model_validate(matcher)
    rk = resolve_key or _missing_key_resolver
    for clause in m.all:
        actuals = resolve_path(value, clause.path)
        expected = rk(key_ref_path(clause.value)) if is_key_ref(clause.value) else clause.value
        if clause.op == "exists":
            if len(actuals) == 0:
                return False
            continue
        if clause.op == "absent":
            if len(actuals) != 0:
                return False
            continue
        if not any(compare(clause.op, a, expected) for a in actuals):
            return False
    return True
