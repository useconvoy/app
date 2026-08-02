/**
 * Shared grader plumbing: canonical hashing (graderVersion), answer-key
 * resolution (KeyRef grammar incl. per-item scope), event ordering, and the
 * EventMatcher/GateMatcher predicates used by trajectory + probe graders.
 */

import { createHash } from 'node:crypto';
import type { ConvoyEvent, EventOfType } from '../runtime/events.ts';
import { matches, resolvePath, type KeyResolver } from '../schema/match.ts';
import type { AnswerKey, EventMatcher, GateMatcher } from '../schema/scenario.ts';
import type { Evidence } from '../schema/verdict.ts';

// ---------------------------------------------------------------------------
// Canonical JSON + hashing — graderVersion = sha256(canonicalJson(spec))
// ---------------------------------------------------------------------------

function sortValue(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(sortValue);
  if (v !== null && typeof v === 'object') {
    const src = v as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const k of Object.keys(src).sort()) {
      if (src[k] !== undefined) out[k] = sortValue(src[k]);
    }
    return out;
  }
  return v;
}

/** Deterministic JSON: object keys sorted recursively, undefined dropped. */
export function canonicalJson(value: unknown): string {
  return JSON.stringify(sortValue(value)) ?? 'null';
}

export function sha256Hex(input: string): string {
  return createHash('sha256').update(input).digest('hex');
}

/** Content hash of a grader spec — stamped on every Verdict it emits. */
export function graderVersionOf(spec: unknown): string {
  return sha256Hex(canonicalJson(spec));
}

// ---------------------------------------------------------------------------
// Item scope
// ---------------------------------------------------------------------------

/**
 * The two names an item carries: `itemId` is the domain key events/tags use
 * (event.itemRef === itemId; '{itemRef}' interpolation); `keyRef` selects the
 * answerKey.perItem subtree ('item.X' KeyRefs resolve through it).
 */
export interface ItemCtx {
  itemId: string;
  keyRef: string;
}

/**
 * KeyRef grammar: 'facts.X' and 'perItem.<id>.X' resolve against the key root;
 * 'item.X' resolves against perItem[currentItem] and is only legal in item
 * scope. Single match unwraps; no match → undefined; multi-match → array.
 */
export function makeKeyResolver(key: AnswerKey, itemCtx: ItemCtx | null): KeyResolver {
  return (path: string) => {
    let target: unknown = key;
    let rest = path;
    if (path === 'item' || path.startsWith('item.')) {
      if (!itemCtx) throw new Error(`KeyRef "${path}" uses item.* outside item scope`);
      target = key.perItem?.[itemCtx.keyRef] ?? {};
      rest = path === 'item' ? '' : path.slice('item.'.length);
    }
    const values = resolvePath(target, rest);
    if (values.length === 0) return undefined;
    return values.length === 1 ? values[0] : values;
  };
}

// ---------------------------------------------------------------------------
// Event ordering + scoping
// ---------------------------------------------------------------------------

/** Order by (ts, seq) — zero-duration sim ties resolve by append order. */
export function orderEvents(events: ConvoyEvent[]): ConvoyEvent[] {
  return [...events].sort((a, b) => (a.ts === b.ts ? a.seq - b.seq : a.ts < b.ts ? -1 : 1));
}

/** Item scope: an event belongs to an item iff event.itemRef === item domain key. */
export function scopeEvents(events: ConvoyEvent[], itemCtx: ItemCtx | null): ConvoyEvent[] {
  const ordered = orderEvents(events);
  return itemCtx ? ordered.filter((e) => e.itemRef === itemCtx.itemId) : ordered;
}

// ---------------------------------------------------------------------------
// Matchers
// ---------------------------------------------------------------------------

/**
 * ArgsMatcher inside an EventMatcher runs against the event's tool-call `args`
 * payload when the event carries one (tool_call / tool_intent / tool_executed /
 * tool_denied) — scenario authors write paths like "to", not "args.to". Events
 * without an `args` field fall back to matching the whole event object.
 */
export function matchEvent(m: EventMatcher, e: ConvoyEvent, resolveKey?: KeyResolver): boolean {
  if (m.type !== undefined) {
    const types = Array.isArray(m.type) ? m.type : [m.type];
    if (!types.includes(e.type)) return false;
  }
  if (m.tool !== undefined && (e as { tool?: string }).tool !== m.tool) return false;
  if (m.itemRef !== undefined && e.itemRef !== m.itemRef) return false;
  if (m.args !== undefined) {
    const argsPayload = (e as { args?: unknown }).args;
    const target = argsPayload !== undefined ? argsPayload : e;
    if (!matches(m.args, target, resolveKey)) return false;
  }
  return true;
}

export function matchGate(
  g: GateMatcher,
  e: EventOfType<'gate_raised'>,
  resolveKey?: KeyResolver
): boolean {
  if (g.kind !== undefined && e.kind !== g.kind) return false;
  if (g.stepTag !== undefined && e.stepTag !== g.stepTag) return false;
  if (g.payload !== undefined && !matches(g.payload, e.payload, resolveKey)) return false;
  return true;
}

// ---------------------------------------------------------------------------
// Evidence + result helpers
// ---------------------------------------------------------------------------

export function evNote(text: string): Evidence {
  return { kind: 'note', text };
}

export function evEvent(eventId: string, note?: string): Evidence {
  return note === undefined ? { kind: 'event', eventId } : { kind: 'event', eventId, note };
}

export function evArtifact(hash: string, excerpt?: string): Evidence {
  return excerpt === undefined ? { kind: 'artifact', hash } : { kind: 'artifact', hash, excerpt };
}

export function evWorld(query: string, result: unknown): Evidence {
  return { kind: 'world', query, result };
}

export function clamp01(n: number): number {
  if (Number.isNaN(n)) return 0;
  return Math.min(1, Math.max(0, n));
}

/** What every grader module returns; index.ts wraps it into a Verdict. */
export interface GraderOutcome {
  status: 'pass' | 'fail' | 'error';
  score: number;
  evidence: Evidence[];
  lowConfidence?: boolean;
  advisory?: boolean;
  costUsd?: number;
}

/** Per-assertion result; a grader's score is the mean of its asserts' scores. */
export interface AssertResult {
  pass: boolean;
  /** 0..1 — binary asserts emit 0|1; checklist emits the weighted fraction. */
  score: number;
  evidence: Evidence[];
}

export function combineAsserts(results: AssertResult[]): GraderOutcome {
  if (results.length === 0) return { status: 'pass', score: 1, evidence: [] };
  const score = clamp01(results.reduce((s, r) => s + r.score, 0) / results.length);
  const failed = results.filter((r) => !r.pass);
  if (failed.length === 0) {
    // Keep at most one supporting evidence item per assert on pass.
    const evidence = results.flatMap((r) => r.evidence.slice(0, 1));
    return { status: 'pass', score: 1, evidence };
  }
  const evidence = failed.flatMap((r) => r.evidence);
  if (evidence.length === 0) evidence.push(evNote('assertion failed (no evidence captured)'));
  return { status: 'fail', score, evidence };
}
