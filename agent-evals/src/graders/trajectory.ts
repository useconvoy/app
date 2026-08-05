/**
 * Trajectory graders — over the ordered (ts, seq) event log. Item-scoped
 * graders see only events with event.itemRef === the item's domain key;
 * run-scoped graders see everything.
 */

import type { ConvoyEvent, EventOfType } from '../runtime/events.ts';
import type { GradeRecord } from '../sandbox/api.ts';
import { compare, matches, type KeyResolver } from '../schema/match.ts';
import { parseDuration, type GraderSpec, type TrajectoryAssertion } from '../schema/scenario.ts';
import {
  combineAsserts,
  evEvent,
  evNote,
  makeKeyResolver,
  matchEvent,
  matchGate,
  scopeEvents,
  type AssertResult,
  type GraderOutcome,
  type ItemCtx,
} from './util.ts';

type TrajectorySpec = Extract<GraderSpec, { grader: 'trajectory' }>;
type GateRaised = EventOfType<'gate_raised'>;
type GateResolved = EventOfType<'gate_resolved'>;

const MAX_EVENT_EVIDENCE = 10;

function binary(pass: boolean, evidence: AssertResult['evidence']): AssertResult {
  return { pass, score: pass ? 1 : 0, evidence };
}

export function evaluateTrajectoryAssertion(
  assertion: TrajectoryAssertion,
  record: GradeRecord,
  itemCtx: ItemCtx | null,
  resolveKey?: KeyResolver
): AssertResult {
  const rk = resolveKey ?? makeKeyResolver(record.answerKey, itemCtx);
  const events = scopeEvents(record.events, itemCtx);

  switch (assertion.kind) {
    case 'never': {
      const hits = events.filter((e) => matchEvent(assertion.where, e, rk));
      if (hits.length === 0) return binary(true, []);
      return binary(
        false,
        hits.slice(0, MAX_EVENT_EVIDENCE).map((e) => evEvent(e.eventId, `forbidden ${e.type} occurred`))
      );
    }

    case 'always': {
      const targets = events.filter((e) => matchEvent(assertion.where, e, rk));
      const violations = targets.filter((e) => !matches(assertion.require, e, rk));
      if (violations.length === 0) {
        const first = targets[0];
        return binary(true, first ? [evEvent(first.eventId)] : []);
      }
      return binary(
        false,
        violations
          .slice(0, MAX_EVENT_EVIDENCE)
          .map((e) => evEvent(e.eventId, `${e.type} violated the required matcher`))
      );
    }

    case 'event_count': {
      const hits = events.filter((e) => matchEvent(assertion.where, e, rk));
      const pass = compare(assertion.op, hits.length, assertion.n);
      const evidence = [
        evNote(`matched ${hits.length} event(s); expected count ${assertion.op} ${assertion.n}`),
        ...hits.slice(0, 3).map((e) => evEvent(e.eventId)),
      ];
      return binary(pass, evidence);
    }

    case 'sequence': {
      const pool = assertion.scope
        ? events.filter((e) => matchEvent(assertion.scope!, e, rk))
        : events;
      const matchedIds: string[] = [];
      let stepIdx = 0;
      for (const e of pool) {
        const step = assertion.steps[stepIdx];
        if (step === undefined) break;
        if (matchEvent(step, e, rk)) {
          matchedIds.push(e.eventId);
          stepIdx += 1;
        }
      }
      if (stepIdx >= assertion.steps.length) {
        return binary(true, matchedIds.slice(0, MAX_EVENT_EVIDENCE).map((id) => evEvent(id)));
      }
      const evidence = [
        evNote(`sequence stalled: step ${stepIdx + 1} of ${assertion.steps.length} never matched`),
        ...matchedIds.slice(-1).map((id) => evEvent(id, 'last matched step')),
      ];
      return binary(false, evidence);
    }

    case 'paused_at_gate': {
      const gatesRaised = events.filter(
        (e): e is GateRaised => e.type === 'gate_raised' && matchGate(assertion.gate, e, rk)
      );
      const resolutions = events.filter((e): e is GateResolved => e.type === 'gate_resolved');
      const approvedAtIdx = (gateId: string): number => {
        const r = resolutions.find(
          (res) =>
            res.gateId === gateId &&
            (res.resolution === 'approve' || res.resolution === 'edit_then_approve')
        );
        return r ? events.indexOf(r) : -1;
      };

      const evidence: AssertResult['evidence'] = [];
      let offenders = 0;
      events.forEach((e, idx) => {
        if (e.type !== 'tool_executed' || !matchEvent(assertion.effect, e, rk)) return;
        const approvedBefore = gatesRaised.some((g) => {
          const gIdx = events.indexOf(g);
          const rIdx = approvedAtIdx(g.gateId);
          return gIdx >= 0 && gIdx < idx && rIdx >= 0 && rIdx < idx;
        });
        if (approvedBefore) return;
        offenders += 1;
        evidence.push(evEvent(e.eventId, 'tool_executed before an approved matching gate'));
        const nearestGate = gatesRaised[gatesRaised.length - 1];
        if (nearestGate) {
          evidence.push(
            evEvent(nearestGate.eventId, 'matching gate was raised but not approved before execution')
          );
        }
      });
      return binary(offenders === 0, evidence);
    }

    case 'eventually': {
      if (events.length === 0) {
        return binary(false, [evNote('eventually: no events in scope')]);
      }
      const first = events[0]!;
      const deadline =
        assertion.withinSim !== undefined
          ? Date.parse(first.ts) + parseDuration(assertion.withinSim)
          : Number.POSITIVE_INFINITY;
      const hit = events.find((e) => matchEvent(assertion.where, e, rk) && Date.parse(e.ts) <= deadline);
      if (hit) return binary(true, [evEvent(hit.eventId)]);
      const late = events.find((e) => matchEvent(assertion.where, e, rk));
      if (late) {
        return binary(false, [
          evEvent(late.eventId, `matched only after the ${assertion.withinSim} window`),
        ]);
      }
      return binary(false, [evNote('eventually: no matching event occurred')]);
    }

    case 'budget_shape': {
      const metricOf = (evs: ConvoyEvent[]): number => {
        if (assertion.metric === 'tool_calls') {
          return evs.filter((e) => e.type === 'tool_call' || e.type === 'tool_executed').length;
        }
        return evs
          .filter((e): e is EventOfType<'budget_debit'> => e.type === 'budget_debit')
          .reduce((sum, e) => sum + (assertion.metric === 'usd' ? e.usd : e.tokens ?? 0), 0);
      };

      if (assertion.per === 'item' && itemCtx === null) {
        const groups = new Map<string, ConvoyEvent[]>();
        for (const e of events) {
          if (e.itemRef === undefined) continue;
          const g = groups.get(e.itemRef) ?? [];
          g.push(e);
          groups.set(e.itemRef, g);
        }
        const failures: AssertResult['evidence'] = [];
        for (const [itemRef, evs] of groups) {
          const total = metricOf(evs);
          if (!compare(assertion.op, total, assertion.limit)) {
            failures.push(
              evNote(`item ${itemRef}: ${assertion.metric}=${total} violates ${assertion.op} ${assertion.limit}`)
            );
          }
        }
        return binary(failures.length === 0, failures);
      }

      const total = metricOf(events);
      const pass = compare(assertion.op, total, assertion.limit);
      return binary(pass, [
        evNote(`${assertion.metric}=${total}; expected ${assertion.op} ${assertion.limit}`),
      ]);
    }
  }
}

export function runTrajectoryGrader(
  spec: TrajectorySpec,
  record: GradeRecord,
  itemCtx: ItemCtx | null
): GraderOutcome {
  const rk = makeKeyResolver(record.answerKey, itemCtx);
  const results = spec.asserts.map((a) => evaluateTrajectoryAssertion(a, record, itemCtx, rk));
  return combineAsserts(results);
}
