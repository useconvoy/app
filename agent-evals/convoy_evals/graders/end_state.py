"""End-state graders — read the exported world bundle + artifact_created events,
NEVER agent self-report. Assertion kinds: artifact_exists, artifact_field,
world_query, checklist (weighted expansion of the answer key's checklist).

Port of src/graders/end-state.ts.
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from ..sandbox.api import GradeRecord, WorldFile
from ..schema.match import compare, is_key_ref, key_ref_path, resolve_path
from ..schema.scenario import ArtifactSelector, ChecklistEntry, EndStateGrader
from .util import (
    AssertResult,
    GraderOutcome,
    ItemCtx,
    KeyResolver,
    as_answer_key,
    binary,
    combine_asserts,
    ev_artifact,
    ev_note,
    ev_world,
    make_key_resolver,
    order_events,
)

# ---------------------------------------------------------------------------
# Artifact lookup (shared with the judge's evidence-bundle builder)
# ---------------------------------------------------------------------------


def interpolate_tag(tag: str, item_ctx: Optional[ItemCtx]) -> str:
    """'{itemRef}' in a tag pattern interpolates the item's domain key."""
    return tag.replace("{itemRef}", item_ctx.itemId) if item_ctx else tag


def find_artifact_events(
    record: GradeRecord, selector: ArtifactSelector, item_ctx: Optional[ItemCtx]
) -> List[Any]:
    """artifact_created events whose tag matches the selector (exact or interpolated)."""
    events = [e for e in order_events(record.events) if e.type == "artifact_created"]
    if selector.tag is None:
        return events
    interpolated = interpolate_tag(selector.tag, item_ctx)
    return [e for e in events if e.tag == selector.tag or e.tag == interpolated]


def file_for_artifact(record: GradeRecord, e: Any) -> Optional[WorldFile]:
    """World-bundle file backing an artifact event, found by content hash."""
    for f in record.world.files:
        if f.hash == e.hash:
            return f
    return None


def artifact_satisfies(record: GradeRecord, e: Any, selector: ArtifactSelector) -> bool:
    """mime/minBytes are checked against the bundle file; a missing file fails a constraint."""
    file = file_for_artifact(record, e)
    if selector.mime is not None:
        mime = file.mime if file is not None else e.mime
        if mime != selector.mime:
            return False
    if selector.minBytes is not None:
        if file is not None:
            nbytes: Optional[int] = len(file.content.encode("utf-8"))
        else:
            nbytes = e.bytes
        if nbytes is None or nbytes < selector.minBytes:
            return False
    return True


# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------


def _describe_selector(selector: ArtifactSelector, item_ctx: Optional[ItemCtx]) -> str:
    parts: List[str] = []
    if selector.tag is not None:
        parts.append("tag=%s" % interpolate_tag(selector.tag, item_ctx))
    if selector.mime is not None:
        parts.append("mime=%s" % selector.mime)
    if selector.minBytes is not None:
        parts.append("minBytes=%s" % selector.minBytes)
    return " ".join(parts) if parts else "(any artifact)"


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def evaluate_end_state_assertion(
    assertion: Any,
    record: GradeRecord,
    item_ctx: Optional[ItemCtx],
    resolve_key: Optional[KeyResolver] = None,
) -> AssertResult:
    rk = resolve_key if resolve_key is not None else make_key_resolver(record.answerKey, item_ctx)

    if assertion.kind == "artifact_exists":
        candidates = find_artifact_events(record, assertion.selector, item_ctx)
        hit = next(
            (e for e in candidates if artifact_satisfies(record, e, assertion.selector)), None
        )
        if hit is not None:
            return binary(True, [ev_artifact(hit.hash, "tag=%s" % hit.tag)])
        if len(candidates) > 0:
            near = candidates[-1]
            return binary(
                False,
                [
                    ev_artifact(near.hash, "tag=%s" % near.tag),
                    ev_note(
                        "artifact matched tag but failed constraints: %s"
                        % _describe_selector(assertion.selector, item_ctx)
                    ),
                ],
            )
        return binary(
            False,
            [ev_note("no artifact matching %s" % _describe_selector(assertion.selector, item_ctx))],
        )

    if assertion.kind == "artifact_field":
        candidates = [
            e
            for e in find_artifact_events(record, assertion.selector, item_ctx)
            if artifact_satisfies(record, e, assertion.selector)
        ]
        artifact = candidates[-1] if candidates else None
        if artifact is None:
            return binary(
                False,
                [
                    ev_note(
                        "no artifact matching %s" % _describe_selector(assertion.selector, item_ctx)
                    )
                ],
            )
        file = file_for_artifact(record, artifact)
        if file is None:
            return binary(
                False,
                [
                    ev_artifact(artifact.hash),
                    ev_note("artifact content not present in world bundle"),
                ],
            )

        if assertion.extract.kind == "json_path":
            try:
                parsed = json.loads(file.content)
            except ValueError:
                return binary(
                    False,
                    [
                        ev_artifact(artifact.hash, file.content[:200]),
                        ev_note("artifact content is not valid JSON for json_path extraction"),
                    ],
                )
            actuals = resolve_path(parsed, assertion.extract.path)
        else:
            m = re.search(assertion.extract.pattern, file.content, re.M | re.S)
            group: Optional[str] = None
            if m is not None:
                try:
                    group = m.group(assertion.extract.group)
                except IndexError:
                    group = None
            actuals = [] if group is None else [group]

        expected = (
            rk(key_ref_path(assertion.expected))
            if is_key_ref(assertion.expected)
            else assertion.expected
        )
        if assertion.op == "exists":
            passed = len(actuals) > 0
        elif assertion.op == "absent":
            passed = len(actuals) == 0
        else:
            passed = any(compare(assertion.op, a, expected) for a in actuals)

        excerpt = "actual=%s" % _dumps(actuals[0] if len(actuals) == 1 else actuals)[:200]
        if passed:
            return binary(True, [ev_artifact(artifact.hash, excerpt)])
        return binary(
            False,
            [
                ev_artifact(artifact.hash, excerpt),
                ev_note("expected %s %s" % (assertion.op, _dumps(expected)[:200])),
            ],
        )

    if assertion.kind == "world_query":
        results = record.worldQuery(assertion.query)
        expected = (
            rk(key_ref_path(assertion.expected))
            if is_key_ref(assertion.expected)
            else assertion.expected
        )
        if assertion.op == "exists":
            passed = len(results) > 0
        elif assertion.op == "absent":
            passed = len(results) == 0
        else:
            passed = any(compare(assertion.op, r, expected) for r in results)
        evidence: List[Any] = [ev_world(assertion.query, results)]
        if not passed and assertion.op not in ("exists", "absent"):
            evidence.append(ev_note("expected %s %s" % (assertion.op, _dumps(expected)[:200])))
        return binary(passed, evidence)

    if assertion.kind == "checklist":
        key = as_answer_key(record.answerKey)
        entries: Optional[List[ChecklistEntry]] = (key.checklists or {}).get(
            assertion.checklistRef
        )
        if entries is None:
            # Grader/harness misconfiguration, not subject failure -> surfaced as error.
            raise ValueError('unknown checklist "%s" in answer key' % assertion.checklistRef)
        total_weight = 0.0
        passed_weight = 0.0
        evidence = []
        for entry in entries:
            weight = entry.weight if entry.weight is not None else 1.0
            total_weight += weight
            sub = evaluate_end_state_assertion(entry.assert_, record, item_ctx, rk)
            if sub.pass_:
                passed_weight += weight
            else:
                evidence.append(
                    ev_note('checklist entry "%s" failed: %s' % (entry.id, entry.description))
                )
                evidence.extend(sub.evidence)
        score = 1.0 if total_weight == 0 else passed_weight / total_weight
        return AssertResult(pass_=score == 1.0, score=score, evidence=evidence)

    raise ValueError("unknown end-state assertion kind: %s" % assertion.kind)


# ---------------------------------------------------------------------------
# Grader entry point
# ---------------------------------------------------------------------------


def run_end_state_grader(
    spec: EndStateGrader, record: GradeRecord, item_ctx: Optional[ItemCtx]
) -> GraderOutcome:
    rk = make_key_resolver(record.answerKey, item_ctx)
    results = [evaluate_end_state_assertion(a, record, item_ctx, rk) for a in spec.asserts]
    return combine_asserts(results)
