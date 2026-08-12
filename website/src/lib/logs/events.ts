/**
 * Cross-run event fan-in for the Logs surface.
 *
 * The honest v1: there is no org-wide events endpoint yet, so
 * the explorer pulls `GET /runs/{id}/events?after=0` for every run the
 * tenant knows, bounded and capped, then merges newest-first.
 * TODO(runtime-D8): replace the fan-in with the org feed and delete the
 * polling machinery here.
 *
 * The notifier owns a sibling fan-in (src/notifier/feed.ts) with cursor
 * semantics this surface does not need; notifier internals are never
 * imported into pages, so the minimal read-and-parse pattern is
 * deliberately duplicated here in page-shaped form (full replay, no
 * cursors, newest first).
 */
import "server-only";

import { controlPlane, type ActorContext } from "@/lib/api/client";
import { listRuns } from "@/lib/api/runs";

/** One runtime event, shaped exactly like the recorded SSE fixtures. */
export interface LogEvent {
  /** Gapless event id, `run_id:seq`. */
  id: string;
  run_id: string;
  seq: number;
  type: string;
  ts: string;
  virtual_ts: string | null;
  sandbox: boolean;
  actor: string;
  actor_type: string;
  payload: Record<string, unknown>;
}

/** Keep any one run from flooding the explorer. */
const MAX_EVENTS_PER_RUN = 500;
/** How many runs are polled at once. */
const CONCURRENCY = 4;
/** How long one run's poll may read before it is cut off. */
const READ_TIMEOUT_MS = 1_500;

/** Newest first: wall time desc, then run id, then per-run seq desc. */
function compareDesc(a: LogEvent, b: LogEvent): number {
  if (a.ts !== b.ts) return a.ts < b.ts ? 1 : -1;
  if (a.run_id !== b.run_id) return a.run_id < b.run_id ? -1 : 1;
  return b.seq - a.seq;
}

/**
 * Parse an events poll body: SSE frames, or a plain JSON array so the same
 * parser covers recorded fixtures and a future non-streaming org feed.
 * Partial trailing frames from a cut-off read are dropped.
 */
export function parseEventsBody(body: string): LogEvent[] {
  const trimmed = body.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("[")) {
    try {
      return JSON.parse(trimmed) as LogEvent[];
    } catch {
      return [];
    }
  }
  const events: LogEvent[] = [];
  for (const line of trimmed.split(/\r?\n/)) {
    if (!line.startsWith("data:")) continue;
    try {
      events.push(JSON.parse(line.slice(5).trim()) as LogEvent);
    } catch {
      // Partial frame from a cut-off read; skip it.
    }
  }
  return events;
}

/** Drain a body stream, keeping whatever arrived if the read is aborted. */
async function readUntilCloseOrAbort(body: ReadableStream<Uint8Array>): Promise<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let text = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (value) text += decoder.decode(value, { stream: true });
      if (done) break;
    }
  } catch {
    // Aborted by the read timeout; the accumulated frames still count.
  } finally {
    reader.releaseLock();
  }
  return text;
}

/**
 * One bounded poll of a run's event stream through the single
 * authenticated edge. Network failures degrade to an empty batch; the
 * explorer renders what it has.
 */
async function fetchRunEvents(actor: ActorContext, runId: string): Promise<LogEvent[]> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), READ_TIMEOUT_MS);
  try {
    const { response } = await controlPlane(actor).GET("/runs/{run_id}/events", {
      params: { path: { run_id: runId }, query: { after: 0 } },
      parseAs: "stream",
      signal: controller.signal,
    });
    if (!response.ok || !response.body) return [];
    return parseEventsBody(await readUntilCloseOrAbort(response.body)).slice(0, MAX_EVENTS_PER_RUN);
  } catch {
    return [];
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Every event the tenant's recent runs have emitted, merged newest first.
 * Polls run in small batches so a long run list cannot open dozens of
 * simultaneous streams.
 */
export async function loadOrgEvents(actor: ActorContext): Promise<LogEvent[]> {
  let runIds: string[] = [];
  try {
    runIds = (await listRuns(actor)).map((run) => run.run_id);
  } catch {
    return [];
  }
  const merged: LogEvent[] = [];
  for (let i = 0; i < runIds.length; i += CONCURRENCY) {
    const batch = runIds.slice(i, i + CONCURRENCY);
    const results = await Promise.all(batch.map((runId) => fetchRunEvents(actor, runId)));
    for (const events of results) merged.push(...events);
  }
  return merged.sort(compareDesc);
}
