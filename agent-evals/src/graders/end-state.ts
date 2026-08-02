/**
 * End-state graders — read the exported world bundle + artifact_created events,
 * NEVER agent self-report. Assertion kinds: artifact_exists, artifact_field,
 * world_query, checklist (weighted expansion of the answer key's checklist).
 */

import { Buffer } from 'node:buffer';
import type { EventOfType } from '../runtime/events.ts';
import type { GradeRecord } from '../sandbox/api.ts';
import type { WorldFile } from '../sandbox/api.ts';
import { compare, isKeyRef, resolvePath, type KeyResolver } from '../schema/match.ts';
import type { ArtifactSelectorSchema, EndStateAssertion, GraderSpec } from '../schema/scenario.ts';
import type { z } from 'zod';
import {
  combineAsserts,
  evArtifact,
  evNote,
  evWorld,
  makeKeyResolver,
  orderEvents,
  type AssertResult,
  type GraderOutcome,
  type ItemCtx,
} from './util.ts';

type ArtifactSelector = z.infer<typeof ArtifactSelectorSchema>;
type ArtifactEvent = EventOfType<'artifact_created'>;
type EndStateSpec = Extract<GraderSpec, { grader: 'end_state' }>;

// ---------------------------------------------------------------------------
// Artifact lookup (shared with the judge's evidence-bundle builder)
// ---------------------------------------------------------------------------

/** '{itemRef}' in a tag pattern interpolates the item's domain key. */
export function interpolateTag(tag: string, itemCtx: ItemCtx | null): string {
  return itemCtx ? tag.replaceAll('{itemRef}', itemCtx.itemId) : tag;
}

/** artifact_created events whose tag matches the selector (exact or interpolated). */
export function findArtifactEvents(
  record: GradeRecord,
  selector: ArtifactSelector,
  itemCtx: ItemCtx | null
): ArtifactEvent[] {
  const events = orderEvents(record.events).filter(
    (e): e is ArtifactEvent => e.type === 'artifact_created'
  );
  if (selector.tag === undefined) return events;
  const interpolated = interpolateTag(selector.tag, itemCtx);
  return events.filter((e) => e.tag === selector.tag || e.tag === interpolated);
}

/** World-bundle file backing an artifact event, found by content hash. */
export function fileForArtifact(record: GradeRecord, e: ArtifactEvent): WorldFile | undefined {
  return record.world.files.find((f) => f.hash === e.hash);
}

/** mime/minBytes are checked against the bundle file; a missing file fails a constraint. */
export function artifactSatisfies(
  record: GradeRecord,
  e: ArtifactEvent,
  selector: ArtifactSelector
): boolean {
  const file = fileForArtifact(record, e);
  if (selector.mime !== undefined) {
    const mime = file?.mime ?? e.mime;
    if (mime !== selector.mime) return false;
  }
  if (selector.minBytes !== undefined) {
    const bytes = file !== undefined ? Buffer.byteLength(file.content, 'utf8') : e.bytes;
    if (bytes === undefined || bytes < selector.minBytes) return false;
  }
  return true;
}

// ---------------------------------------------------------------------------
// Assertion evaluation
// ---------------------------------------------------------------------------

function describeSelector(selector: ArtifactSelector, itemCtx: ItemCtx | null): string {
  const parts: string[] = [];
  if (selector.tag !== undefined) parts.push(`tag=${interpolateTag(selector.tag, itemCtx)}`);
  if (selector.mime !== undefined) parts.push(`mime=${selector.mime}`);
  if (selector.minBytes !== undefined) parts.push(`minBytes=${selector.minBytes}`);
  return parts.length ? parts.join(' ') : '(any artifact)';
}

function binary(pass: boolean, evidence: AssertResult['evidence']): AssertResult {
  return { pass, score: pass ? 1 : 0, evidence };
}

export function evaluateEndStateAssertion(
  assertion: EndStateAssertion,
  record: GradeRecord,
  itemCtx: ItemCtx | null,
  resolveKey?: KeyResolver
): AssertResult {
  const rk = resolveKey ?? makeKeyResolver(record.answerKey, itemCtx);

  switch (assertion.kind) {
    case 'artifact_exists': {
      const candidates = findArtifactEvents(record, assertion.selector, itemCtx);
      const hit = candidates.find((e) => artifactSatisfies(record, e, assertion.selector));
      if (hit) return binary(true, [evArtifact(hit.hash, `tag=${hit.tag}`)]);
      if (candidates.length > 0) {
        const near = candidates[candidates.length - 1]!;
        return binary(false, [
          evArtifact(near.hash, `tag=${near.tag}`),
          evNote(`artifact matched tag but failed constraints: ${describeSelector(assertion.selector, itemCtx)}`),
        ]);
      }
      return binary(false, [
        evNote(`no artifact matching ${describeSelector(assertion.selector, itemCtx)}`),
      ]);
    }

    case 'artifact_field': {
      const candidates = findArtifactEvents(record, assertion.selector, itemCtx).filter((e) =>
        artifactSatisfies(record, e, assertion.selector)
      );
      const artifact = candidates[candidates.length - 1];
      if (!artifact) {
        return binary(false, [
          evNote(`no artifact matching ${describeSelector(assertion.selector, itemCtx)}`),
        ]);
      }
      const file = fileForArtifact(record, artifact);
      if (!file) {
        return binary(false, [
          evArtifact(artifact.hash),
          evNote('artifact content not present in world bundle'),
        ]);
      }

      let actuals: unknown[];
      if (assertion.extract.kind === 'json_path') {
        let parsed: unknown;
        try {
          parsed = JSON.parse(file.content);
        } catch {
          return binary(false, [
            evArtifact(artifact.hash, file.content.slice(0, 200)),
            evNote('artifact content is not valid JSON for json_path extraction'),
          ]);
        }
        actuals = resolvePath(parsed, assertion.extract.path);
      } else {
        const m = new RegExp(assertion.extract.pattern, 'ms').exec(file.content);
        const group = m?.[assertion.extract.group];
        actuals = group === undefined ? [] : [group];
      }

      const expected = isKeyRef(assertion.expected) ? rk(assertion.expected.$key) : assertion.expected;
      let pass: boolean;
      if (assertion.op === 'exists') pass = actuals.length > 0;
      else if (assertion.op === 'absent') pass = actuals.length === 0;
      else pass = actuals.some((a) => compare(assertion.op, a, expected));

      const excerpt = `actual=${JSON.stringify(actuals.length === 1 ? actuals[0] : actuals)?.slice(0, 200)}`;
      if (pass) return binary(true, [evArtifact(artifact.hash, excerpt)]);
      return binary(false, [
        evArtifact(artifact.hash, excerpt),
        evNote(`expected ${assertion.op} ${JSON.stringify(expected)?.slice(0, 200) ?? 'undefined'}`),
      ]);
    }

    case 'world_query': {
      const results = record.worldQuery(assertion.query);
      const expected = isKeyRef(assertion.expected) ? rk(assertion.expected.$key) : assertion.expected;
      let pass: boolean;
      if (assertion.op === 'exists') pass = results.length > 0;
      else if (assertion.op === 'absent') pass = results.length === 0;
      else pass = results.some((r) => compare(assertion.op, r, expected));
      const evidence = [evWorld(assertion.query, results)];
      if (!pass && assertion.op !== 'exists' && assertion.op !== 'absent') {
        evidence.push(evNote(`expected ${assertion.op} ${JSON.stringify(expected)?.slice(0, 200) ?? 'undefined'}`));
      }
      return binary(pass, evidence);
    }

    case 'checklist': {
      const entries = record.answerKey.checklists?.[assertion.checklistRef];
      if (!entries) {
        // Grader/harness misconfiguration, not subject failure → surfaced as error.
        throw new Error(`unknown checklist "${assertion.checklistRef}" in answer key`);
      }
      let totalWeight = 0;
      let passedWeight = 0;
      const evidence: AssertResult['evidence'] = [];
      for (const entry of entries) {
        const weight = entry.weight ?? 1;
        totalWeight += weight;
        const sub = evaluateEndStateAssertion(entry.assert, record, itemCtx, rk);
        if (sub.pass) {
          passedWeight += weight;
        } else {
          evidence.push(evNote(`checklist entry "${entry.id}" failed: ${entry.description}`));
          evidence.push(...sub.evidence);
        }
      }
      const score = totalWeight === 0 ? 1 : passedWeight / totalWeight;
      return { pass: score === 1, score, evidence };
    }
  }
}

// ---------------------------------------------------------------------------
// Grader entry point
// ---------------------------------------------------------------------------

export function runEndStateGrader(
  spec: EndStateSpec,
  record: GradeRecord,
  itemCtx: ItemCtx | null
): GraderOutcome {
  const rk = makeKeyResolver(record.answerKey, itemCtx);
  const results = spec.asserts.map((a) => evaluateEndStateAssertion(a, record, itemCtx, rk));
  return combineAsserts(results);
}

export type { ArtifactEvent, ArtifactSelector };
