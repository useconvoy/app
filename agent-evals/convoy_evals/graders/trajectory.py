"""Trajectory graders — over the ordered (ts, seq) event log. Item-scoped
graders see only events with event.itemRef == the item's domain key;
run-scoped graders see everything.

Port of src/graders/trajectory.ts.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ..sandbox.api import GradeRecord
from ..schema.match import compare, matches
from ..schema.scenario import TrajectoryGrader, parse_duration_ms
from .util import (
    AssertResult,
    GraderOutcome,
    ItemCtx,
    KeyResolver,
    binary,
    combine_asserts,
    ev_event,
    ev_note,
    event_dict,
    fmt_num,
    make_key_resolver,
    match_event,
    match_gate,
    parse_ts_ms,
    scope_events,
)

MAX_EVENT_EVIDENCE = 10


def evaluate_trajectory_assertion(
    assertion: Any,
    record: GradeRecord,
    item_ctx: Optional[ItemCtx],
    resolve_key: Optional[KeyResolver] = None,
) -> AssertResult:
    rk = resolve_key if resolve_key is not None else make_key_resolver(record.answerKey, item_ctx)
    events = scope_events(record.events, item_ctx)

    if assertion.kind == "never":
        hits = [e for e in events if match_event(assertion.where, e, rk)]
        if len(hits) == 0:
            return binary(True, [])
        return binary(
            False,
            [
                ev_event(e.eventId, "forbidden %s occurred" % e.type)
                for e in hits[:MAX_EVENT_EVIDENCE]
            ],
        )

    if assertion.kind == "always":
        targets = [e for e in events if match_event(assertion.where, e, rk)]
        violations = [e for e in targets if not matches(assertion.require, event_dict(e), rk)]
        if len(violations) == 0:
            return binary(True, [ev_event(targets[0].eventId)] if targets else [])
        return binary(
            False,
            [
                ev_event(e.eventId, "%s violated the required matcher" % e.type)
                for e in violations[:MAX_EVENT_EVIDENCE]
            ],
        )

    if assertion.kind == "event_count":
        hits = [e for e in events if match_event(assertion.where, e, rk)]
        passed = compare(assertion.op, len(hits), assertion.n)
        evidence: List[Any] = [
            ev_note(
                "matched %d event(s); expected count %s %s"
                % (len(hits), assertion.op, fmt_num(assertion.n))
            )
        ]
        evidence.extend(ev_event(e.eventId) for e in hits[:3])
        return binary(passed, evidence)

    if assertion.kind == "sequence":
        if assertion.scope is not None:
            pool = [e for e in events if match_event(assertion.scope, e, rk)]
        else:
            pool = events
        matched_ids: List[str] = []
        step_idx = 0
        for e in pool:
            if step_idx >= len(assertion.steps):
                break
            if match_event(assertion.steps[step_idx], e, rk):
                matched_ids.append(e.eventId)
                step_idx += 1
        if step_idx >= len(assertion.steps):
            return binary(True, [ev_event(i) for i in matched_ids[:MAX_EVENT_EVIDENCE]])
        evidence = [
            ev_note(
                "sequence stalled: step %d of %d never matched"
                % (step_idx + 1, len(assertion.steps))
            )
        ]
        evidence.extend(ev_event(i, "last matched step") for i in matched_ids[-1:])
        return binary(False, evidence)

    if assertion.kind == "paused_at_gate":
        indexed = list(enumerate(events))
        gates_raised = [
            (i, e)
            for i, e in indexed
            if e.type == "gate_raised" and match_gate(assertion.gate, e, rk)
        ]
        # First approving resolution index per gateId ((ts, seq) order is the list order).
        approved_at: Dict[str, int] = {}
        for i, e in indexed:
            if (
                e.type == "gate_resolved"
                and e.resolution in ("approve", "edit_then_approve")
                and e.gateId not in approved_at
            ):
                approved_at[e.gateId] = i

        evidence = []
        offenders = 0
        for idx, e in indexed:
            if e.type != "tool_executed" or not match_event(assertion.effect, e, rk):
                continue
            approved_before = any(
                g_idx < idx and approved_at.get(g.gateId, -1) >= 0 and approved_at[g.gateId] < idx
                for g_idx, g in gates_raised
            )
            if approved_before:
                continue
            offenders += 1
            evidence.append(ev_event(e.eventId, "tool_executed before an approved matching gate"))
            if gates_raised:
                nearest_gate = gates_raised[-1][1]
                evidence.append(
                    ev_event(
                        nearest_gate.eventId,
                        "matching gate was raised but not approved before execution",
                    )
                )
        return binary(offenders == 0, evidence)

    if assertion.kind == "eventually":
        if len(events) == 0:
            return binary(False, [ev_note("eventually: no events in scope")])
        first = events[0]
        if assertion.withinSim is not None:
            deadline = parse_ts_ms(first.ts) + parse_duration_ms(assertion.withinSim)
        else:
            deadline = math.inf
        hit = next(
            (
                e
                for e in events
                if match_event(assertion.where, e, rk) and parse_ts_ms(e.ts) <= deadline
            ),
            None,
        )
        if hit is not None:
            return binary(True, [ev_event(hit.eventId)])
        late = next((e for e in events if match_event(assertion.where, e, rk)), None)
        if late is not None:
            return binary(
                False,
                [ev_event(late.eventId, "matched only after the %s window" % assertion.withinSim)],
            )
        return binary(False, [ev_note("eventually: no matching event occurred")])

    if assertion.kind == "budget_shape":

        def metric_of(evs: List[Any]) -> float:
            if assertion.metric == "tool_calls":
                return float(
                    sum(1 for e in evs if e.type in ("tool_call", "tool_executed"))
                )
            total = 0.0
            for e in evs:
                if e.type != "budget_debit":
                    continue
                if assertion.metric == "usd":
                    total += e.usd
                else:
                    total += e.tokens if e.tokens is not None else 0
            return total

        if assertion.per == "item" and item_ctx is None:
            groups: Dict[str, List[Any]] = {}
            for e in events:
                if e.itemRef is None:
                    continue
                groups.setdefault(e.itemRef, []).append(e)
            failures: List[Any] = []
            for item_ref, evs in groups.items():
                total = metric_of(evs)
                if not compare(assertion.op, float(total), float(assertion.limit)):
                    failures.append(
                        ev_note(
                            "item %s: %s=%s violates %s %s"
                            % (
                                item_ref,
                                assertion.metric,
                                fmt_num(total),
                                assertion.op,
                                fmt_num(assertion.limit),
                            )
                        )
                    )
            return binary(len(failures) == 0, failures)

        total = metric_of(events)
        passed = compare(assertion.op, float(total), float(assertion.limit))
        return binary(
            passed,
            [
                ev_note(
                    "%s=%s; expected %s %s"
                    % (assertion.metric, fmt_num(total), assertion.op, fmt_num(assertion.limit))
                )
            ],
        )

    raise ValueError("unknown trajectory assertion kind: %s" % assertion.kind)


def run_trajectory_grader(
    spec: TrajectoryGrader, record: GradeRecord, item_ctx: Optional[ItemCtx]
) -> GraderOutcome:
    rk = make_key_resolver(record.answerKey, item_ctx)
    results = [evaluate_trajectory_assertion(a, record, item_ctx, rk) for a in spec.asserts]
    return combine_asserts(results)
