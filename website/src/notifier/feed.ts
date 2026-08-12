/**
 * The notifier's event feed.
 *
 * The intended source is the runtime's org-wide `GET /events?after=seq`
 * (a requested runtime addition). That endpoint does not exist yet, so the consumer
 * is written against the small `EventFeed` interface below and the fan-in
 * fallback here polls `GET /runs/{id}/events?after={per-run cursor}` for
 * every run id the tenant knows, merging the results into one ordered feed.
 * TODO(runtime-D8): replace `FanInRunFeed` with a thin adapter over the org
 * feed and delete the fan-in machinery; the interface stays.
 *
 * Cursor semantics of the fan-in fallback: `notifier_state.cursor_seq` is
 * org-global, so per-run cursors are deliberately NOT persisted (no jsonb
 * smuggling into a bigint column). The org cursor is the max synthetic seq
 * this feed has handed out; per-run cursors live in process memory only.
 * After a restart the per-run cursors reset and already-seen events replay,
 * which is safe because notification writes are idempotent on the event id
 * (`run_id:seq`) and the org cursor only ever moves forward (GREATEST in
 * the consumer). Replays cost a little work, never duplicate rows.
 *
 * This module must load in two worlds: the Next.js server and the
 * standalone worker under `npm run notifier` (tsx). The generated client
 * helper in src/lib/api/client.ts is marked `server-only`, which throws
 * outside a React server bundle, so this module builds its own
 * openapi-fetch client from the same generated `paths` types and mirrors
 * the auth bridge headers (bearer + actor + tenant). Same edge, same
 * generated contract, no hand-written domain types.
 */
import createClient from "openapi-fetch";

import type { paths } from "../lib/api/schema";

/** One runtime event, shaped exactly like the recorded SSE fixtures. */
export interface RunEvent {
  /** Gapless event id, `run_id:seq`; notification idempotency rides it. */
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

/** A feed event stamped with the org-wide (possibly synthetic) cursor. */
export interface OrgFeedEvent {
  event: RunEvent;
  orgSeq: number;
}

/** What the consumer is written against; the runtime's org feed will slot in here. */
export interface EventFeed {
  /** Events after the given org cursor, in org-cursor order. */
  pull(afterOrgSeq: number): Promise<OrgFeedEvent[]>;
}

/** One row of the control plane's run list the feed cares about. */
export interface FeedRunRow {
  runId: string;
  agentId?: string;
}

export interface FanInRunFeedOptions {
  tenantId: string;
  /** Service identity for the read-only bridge headers. */
  actorId?: string;
  baseUrl?: string;
  token?: string;
  /** Runs to poll; defaults to the control plane's GET /runs list. */
  listRuns?: (tenantId: string) => Promise<FeedRunRow[]>;
  /** How long one run's event poll may read before we cut it off. */
  readTimeoutMs?: number;
}

/** Order merged events by wall time, then run id, then per-run seq. */
function compareEvents(a: RunEvent, b: RunEvent): number {
  if (a.ts !== b.ts) return a.ts < b.ts ? -1 : 1;
  if (a.run_id !== b.run_id) return a.run_id < b.run_id ? -1 : 1;
  return a.seq - b.seq;
}

/**
 * Parse an events poll body. The endpoint streams SSE frames; a plain JSON
 * array is also accepted so the same parser covers a future non-streaming
 * org feed and recorded fixtures. Partial trailing frames (a cut-off read)
 * are dropped; the per-run cursor simply does not advance past them.
 */
export function parseFeedBody(body: string): RunEvent[] {
  const trimmed = body.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("[")) {
    try {
      return JSON.parse(trimmed) as RunEvent[];
    } catch {
      return [];
    }
  }
  const events: RunEvent[] = [];
  for (const line of trimmed.split(/\r?\n/)) {
    if (!line.startsWith("data:")) continue;
    try {
      events.push(JSON.parse(line.slice(5).trim()) as RunEvent);
    } catch {
      // Partial frame from a cut-off read; skip it.
    }
  }
  return events;
}

export class FanInRunFeed implements EventFeed {
  private readonly tenantId: string;
  private readonly listRuns: (tenantId: string) => Promise<FeedRunRow[]>;
  private readonly readTimeoutMs: number;
  private readonly client: ReturnType<typeof createClient<paths>>;
  /** Highest per-run seq already merged, in memory only (see docstring). */
  private readonly runCursors = new Map<string, number>();
  /** run -> agent attribution from the latest list pull; routing reads it. */
  private readonly runAgents = new Map<string, string>();
  /** Highest synthetic org seq handed out so far. */
  private orgSeq = 0;

  constructor(options: FanInRunFeedOptions) {
    this.tenantId = options.tenantId;
    this.listRuns = options.listRuns ?? ((tenantId) => this.fetchRunList(tenantId));
    this.readTimeoutMs = options.readTimeoutMs ?? 1_500;
    const token = options.token ?? process.env.CONVOY_CONTROL_PLANE_TOKEN;
    if (!token) {
      throw new Error("CONVOY_CONTROL_PLANE_TOKEN is not configured");
    }
    this.client = createClient<paths>({
      baseUrl: options.baseUrl ?? process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Actor-Id": options.actorId ?? "system:notifier",
        "X-Tenant-Id": options.tenantId,
      },
    });
  }

  /** The Agent a run maps to, from the latest run-list pull. */
  agentIdForRun(runId: string): string | undefined {
    return this.runAgents.get(runId);
  }

  /** The tenant's runs from the control plane's list endpoint. */
  private async fetchRunList(tenantId: string): Promise<FeedRunRow[]> {
    void tenantId; // the client is already tenant-scoped by its headers
    try {
      const { data } = await this.client.GET("/runs", {
        params: { query: { limit: 100 } },
      });
      return (data?.runs ?? []).map((run) => ({
        runId: run.run_id,
        agentId: run.agent_id ?? undefined,
      }));
    } catch {
      return [];
    }
  }

  async pull(afterOrgSeq: number): Promise<OrgFeedEvent[]> {
    // The persisted org cursor may be ahead of this instance (fresh start):
    // adopt it so synthetic seqs never go backward.
    if (afterOrgSeq > this.orgSeq) this.orgSeq = afterOrgSeq;
    const merged: RunEvent[] = [];
    const rows = await this.listRuns(this.tenantId);
    for (const row of rows) {
      if (row.agentId) this.runAgents.set(row.runId, row.agentId);
    }
    for (const { runId } of rows) {
      const after = this.runCursors.get(runId) ?? 0;
      const events = await this.fetchRunEvents(runId, after);
      let cursor = after;
      for (const event of events) {
        // Double-guard the server-side `after` filter.
        if (event.seq <= after) continue;
        merged.push(event);
        if (event.seq > cursor) cursor = event.seq;
      }
      if (cursor > after) this.runCursors.set(runId, cursor);
    }
    merged.sort(compareEvents);
    return merged.map((event) => ({ event, orgSeq: ++this.orgSeq }));
  }

  /**
   * One bounded poll of a run's event stream. Network failures and cut-off
   * reads degrade to an empty batch; the next tick retries from the same
   * per-run cursor.
   */
  private async fetchRunEvents(runId: string, after: number): Promise<RunEvent[]> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.readTimeoutMs);
    try {
      const { response } = await this.client.GET("/runs/{run_id}/events", {
        params: { path: { run_id: runId }, query: { after } },
        parseAs: "stream",
        signal: controller.signal,
      });
      if (!response.ok || !response.body) return [];
      return parseFeedBody(await readUntilCloseOrAbort(response.body));
    } catch {
      return [];
    } finally {
      clearTimeout(timer);
    }
  }
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
