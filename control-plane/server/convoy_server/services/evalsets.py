from __future__ import annotations

import hashlib
import json
from typing import Any

from convoy_agent.scoring import DEFAULT_THRESHOLDS, MATCH_KINDS
from sqlalchemy.orm import Session as DbSession

from ..ids import new_id
from ..models import EvalSet


class EvalSetError(ValueError):
    pass


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for i, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise EvalSetError(f"line {i}: invalid JSON ({e.msg})") from e
        cases.append(obj)
    return cases


def validate_cases(cases: list[dict[str, Any]]) -> None:
    if not cases:
        raise EvalSetError("eval set has no cases")
    seen: set[str] = set()
    for c in cases:
        cid = c.get("id")
        if not cid or not isinstance(cid, str):
            raise EvalSetError("every case needs a string id")
        if cid in seen:
            raise EvalSetError(f"duplicate case id {cid}")
        seen.add(cid)
        if "prompt" not in c:
            raise EvalSetError(f"case {cid} needs a prompt")
        if c.get("match", "contains") not in MATCH_KINDS:
            raise EvalSetError(f"case {cid}: unknown match kind {c.get('match')}")


def content_hash(cases: list[dict[str, Any]], thresholds: dict[str, Any], scorer: dict[str, Any]) -> str:
    payload = json.dumps(
        {"cases": cases, "thresholds": thresholds, "scorer": scorer}, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def create_eval_set(
    db: DbSession,
    *,
    name: str,
    version: str,
    cases: list[dict[str, Any]],
    thresholds: dict[str, Any] | None,
    scorer: dict[str, Any] | None,
    description: str = "",
    created_by: str | None = None,
) -> EvalSet:
    validate_cases(cases)
    th = dict(DEFAULT_THRESHOLDS)
    th.update(thresholds or {})
    sc = {"version": 1, "deterministic": True, **(scorer or {})}
    row = EvalSet(
        id=new_id("evs"),
        name=name,
        version=version,
        content_hash=content_hash(cases, th, sc),
        cases=cases,
        thresholds=th,
        scorer=sc,
        description=description,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def to_jsonl(es: EvalSet) -> str:
    return "\n".join(json.dumps(c, sort_keys=True) for c in es.cases) + "\n"
