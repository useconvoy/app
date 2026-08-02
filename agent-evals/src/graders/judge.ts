/**
 * Fresh-context LLM judge — pinned model + content-addressed prompt.
 *
 * Isolation guarantee lives in the TYPE: JudgeInput has constructors only for
 * artifact / key_excerpt / world — there is deliberately no way to feed the
 * judge a transcript, messages, plan text, or model_call events. Keep it that
 * way.
 *
 * cacheKey = sha256(promptHash + modelId + bundleHash). cache-only mode (PR
 * CI) NEVER bills the API: a miss is a loud 'error' verdict. live mode makes
 * N=samples temperature-0 calls, majority verdict, mean score, 2-1 split →
 * lowConfidence, and writes the aggregated result back to the cache.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { GradeRecord } from '../sandbox/api.ts';
import type { GraderSpec } from '../schema/scenario.ts';
import type { Evidence } from '../schema/verdict.ts';
import { artifactSatisfies, fileForArtifact, findArtifactEvents } from './end-state.ts';
import { judgeIsCalibrated, type CalibrationStore } from './calibration.ts';
import {
  canonicalJson,
  clamp01,
  evNote,
  makeKeyResolver,
  sha256Hex,
  type GraderOutcome,
  type ItemCtx,
} from './util.ts';

export type JudgeSpec = Extract<GraderSpec, { grader: 'judge' }>;

export interface JudgeOpts {
  cacheDir?: string;
  mode: 'cache-only' | 'live';
  /** When absent or the judge misses thresholds → verdicts are advisory. */
  calibration?: CalibrationStore | null;
}

/** Aggregated 3-sample result — the unit the cache stores (one JSON file per key). */
export interface JudgeCacheEntry {
  verdicts: Array<'pass' | 'fail'>;
  score: number;
  rationales: string[];
}

// ---------------------------------------------------------------------------
// Evidence bundle — built STRICTLY from spec.inputs
// ---------------------------------------------------------------------------

export function buildEvidenceBundle(
  spec: JudgeSpec,
  record: GradeRecord,
  itemCtx: ItemCtx | null
): unknown[] {
  const rk = makeKeyResolver(record.answerKey, itemCtx);
  return spec.inputs.map((input) => {
    switch (input.kind) {
      case 'artifact': {
        const hits = findArtifactEvents(record, input.selector, itemCtx).filter((e) =>
          artifactSatisfies(record, e, input.selector)
        );
        const latest = hits[hits.length - 1];
        const file = latest ? fileForArtifact(record, latest) : undefined;
        return {
          kind: 'artifact',
          tag: latest?.tag ?? null,
          hash: latest?.hash ?? null,
          content: file?.content ?? null,
        };
      }
      case 'key_excerpt':
        return { kind: 'key_excerpt', ref: input.ref.$key, value: rk(input.ref.$key) };
      case 'world':
        return { kind: 'world', query: input.query, result: record.worldQuery(input.query) };
    }
  });
}

/** sha256(promptHash + modelId + bundleHash) — exported so tests can pre-seed the cache. */
export function judgeCacheKey(spec: JudgeSpec, record: GradeRecord, itemCtx: ItemCtx | null): string {
  const bundleHash = sha256Hex(canonicalJson(buildEvidenceBundle(spec, record, itemCtx)));
  return sha256Hex(spec.promptHash + spec.model + bundleHash);
}

// ---------------------------------------------------------------------------
// Aggregation
// ---------------------------------------------------------------------------

function outcomeFromEntry(entry: JudgeCacheEntry, advisory: boolean): GraderOutcome {
  const passes = entry.verdicts.filter((v) => v === 'pass').length;
  const fails = entry.verdicts.length - passes;
  const status: 'pass' | 'fail' = passes > fails ? 'pass' : 'fail';
  const evidence: Evidence[] = entry.rationales.map((text, sampleIdx) => ({
    kind: 'judge_rationale' as const,
    sampleIdx,
    text,
  }));
  if (evidence.length === 0) evidence.push(evNote('judge verdict with no stored rationale'));
  const outcome: GraderOutcome = {
    status,
    score: clamp01(entry.score),
    evidence,
  };
  if (passes > 0 && fails > 0) outcome.lowConfidence = true;
  if (advisory) outcome.advisory = true;
  return outcome;
}

// ---------------------------------------------------------------------------
// Live sampling (Anthropic messages API via fetch)
// ---------------------------------------------------------------------------

interface SampleResult {
  verdict: 'pass' | 'fail';
  score: number;
  rationale: string;
}

function parseSample(text: string, passAt: number): SampleResult {
  const jsonMatch = /\{[\s\S]*\}/.exec(text);
  let verdict: 'pass' | 'fail' | null = null;
  let score = 0;
  let rationale = text.slice(0, 500);
  if (jsonMatch) {
    try {
      const parsed = JSON.parse(jsonMatch[0]) as Record<string, unknown>;
      if (parsed.verdict === 'pass' || parsed.verdict === 'fail') verdict = parsed.verdict;
      if (typeof parsed.score === 'number') score = clamp01(parsed.score);
      if (typeof parsed.rationale === 'string') rationale = parsed.rationale;
    } catch {
      // fall through to score-threshold verdict
    }
  }
  return { verdict: verdict ?? (score >= passAt ? 'pass' : 'fail'), score, rationale };
}

async function sampleLive(spec: JudgeSpec, bundle: unknown[], apiKey: string): Promise<JudgeCacheEntry> {
  const prompt = readFileSync(spec.promptFile, 'utf8');
  const promptHash = sha256Hex(prompt);
  if (promptHash !== spec.promptHash) {
    throw new Error(
      `judge prompt hash mismatch: file ${spec.promptFile} hashes to ${promptHash}, spec pins ${spec.promptHash}`
    );
  }
  const rubricText = spec.rubric
    .map((r) => `- [${r.id}] (weight ${r.weight}) ${r.criterion}`)
    .join('\n');
  const userContent = [
    'Grade the evidence bundle against the rubric.',
    'Respond with ONLY a JSON object: {"verdict": "pass"|"fail", "score": 0..1, "rationale": "..."}.',
    `\nRubric:\n${rubricText}`,
    `\nEvidence bundle:\n${JSON.stringify(bundle, null, 2)}`,
  ].join('\n');

  const verdicts: Array<'pass' | 'fail'> = [];
  const rationales: string[] = [];
  let scoreSum = 0;
  for (let i = 0; i < spec.samples; i += 1) {
    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: spec.model,
        max_tokens: 1024,
        temperature: 0,
        system: prompt,
        messages: [{ role: 'user', content: userContent }],
      }),
    });
    if (!res.ok) {
      throw new Error(`judge model call failed: HTTP ${res.status} ${await res.text()}`);
    }
    const body = (await res.json()) as { content?: Array<{ type: string; text?: string }> };
    const text = (body.content ?? [])
      .filter((b) => b.type === 'text' && typeof b.text === 'string')
      .map((b) => b.text)
      .join('\n');
    const sample = parseSample(text, spec.passAt);
    verdicts.push(sample.verdict);
    rationales.push(sample.rationale);
    scoreSum += sample.score;
  }
  return {
    verdicts,
    score: spec.samples === 0 ? 0 : scoreSum / spec.samples,
    rationales,
  };
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export async function runJudge(
  spec: JudgeSpec,
  record: GradeRecord,
  itemCtx: ItemCtx | null,
  opts: JudgeOpts
): Promise<GraderOutcome> {
  const advisory = !judgeIsCalibrated(
    opts.calibration ?? { records: [] },
    spec.judgeId,
    spec.promptHash
  );
  const cacheKey = judgeCacheKey(spec, record, itemCtx);
  const cachePath = opts.cacheDir ? join(opts.cacheDir, `${cacheKey}.json`) : null;

  if (cachePath && existsSync(cachePath)) {
    const entry = JSON.parse(readFileSync(cachePath, 'utf8')) as JudgeCacheEntry;
    return outcomeFromEntry(entry, advisory);
  }

  if (opts.mode === 'cache-only') {
    const outcome: GraderOutcome = {
      status: 'error',
      score: 0,
      evidence: [evNote('judge cache miss (live disabled)')],
    };
    if (advisory) outcome.advisory = true;
    return outcome;
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    const outcome: GraderOutcome = {
      status: 'error',
      score: 0,
      evidence: [evNote('live judge requested but ANTHROPIC_API_KEY is not set')],
    };
    if (advisory) outcome.advisory = true;
    return outcome;
  }

  const bundle = buildEvidenceBundle(spec, record, itemCtx);
  const entry = await sampleLive(spec, bundle, apiKey);
  if (cachePath) {
    mkdirSync(opts.cacheDir!, { recursive: true });
    writeFileSync(cachePath, JSON.stringify(entry, null, 2));
  }
  return outcomeFromEntry(entry, advisory);
}
