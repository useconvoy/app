"use client";

/**
 * Live run events over the SSE proxy. EventSource handles most
 * reconnection natively (re-sending Last-Event-ID), so this hook
 * accumulates events, dedupes replays by seq, and tracks connection state
 * so the UI can surface disconnects. Silent staleness is a bug (CLAUDE.md
 * rule 11): consumers must render a DisconnectBanner whenever `connected`
 * is false and the stream is not `done`.
 *
 * Going offline does not sever an idle SSE socket, so EventSource alone
 * would sit silently stale; the browser's offline/online events cover that
 * path, reconnecting with `?after=<last seq>` (the proxy honors it the
 * same way it honors Last-Event-ID).
 */
import { useEffect, useMemo, useState } from "react";

import { eventLabels } from "@/lexicon";
import type { RunStreamEvent } from "@/lib/runs/status";

export interface RunEventStream {
  /** Every event received so far, ordered by seq. */
  events: RunStreamEvent[];
  /** False after a dropped connection, until the stream reconnects. */
  connected: boolean;
  /** Timestamp of the newest event, for the disconnect banner. */
  lastEventAt: string | null;
  /** The stream ended after run_completed/run_failed: expect no more. */
  done: boolean;
}

export function useRunEvents(runId: string): RunEventStream {
  const [events, setEvents] = useState<RunStreamEvent[]>([]);
  const [dropped, setDropped] = useState(false);

  useEffect(() => {
    // Collected per subscription: a new run's stream replaces the state
    // wholesale on its first event, so nothing stale survives a run switch.
    const collected: RunStreamEvent[] = [];
    const seen = new Set<number>();
    let source: EventSource | null = null;
    let finished = false;

    const receive = (message: MessageEvent<string>) => {
      let event: RunStreamEvent;
      try {
        event = JSON.parse(message.data) as RunStreamEvent;
      } catch {
        return;
      }
      if (typeof event.seq !== "number" || seen.has(event.seq)) return;
      seen.add(event.seq);
      collected.push(event);
      collected.sort((a, b) => a.seq - b.seq);
      setEvents([...collected]);
      if (event.type === "run_completed" || event.type === "run_failed") {
        // The runtime closes the stream after a terminal event; without
        // this, EventSource would reconnect forever against a closed run.
        finished = true;
        source?.close();
      }
    };

    const connect = (after?: number) => {
      const base = `/api/runs/${encodeURIComponent(runId)}/events`;
      source = new EventSource(after === undefined ? base : `${base}?after=${after}`);
      // SSE frames arrive typed (`event: step_done`), which onmessage never
      // sees; listen per catalog entry, with onmessage for untyped frames.
      for (const type of Object.keys(eventLabels)) source.addEventListener(type, receive);
      source.onmessage = receive;
      source.onopen = () => setDropped(false);
      source.onerror = () => {
        if (!finished) setDropped(true);
      };
    };
    connect();

    const onOffline = () => {
      if (finished) return;
      // The socket is dead even though EventSource has not noticed; say so
      // and stop its blind retries until the network is back.
      setDropped(true);
      source?.close();
    };
    const onOnline = () => {
      if (finished) return;
      source?.close();
      connect(collected.length > 0 ? collected[collected.length - 1]!.seq : undefined);
    };
    window.addEventListener("offline", onOffline);
    window.addEventListener("online", onOnline);

    return () => {
      window.removeEventListener("offline", onOffline);
      window.removeEventListener("online", onOnline);
      source?.close();
    };
  }, [runId]);

  const last = events.length > 0 ? events[events.length - 1]! : null;
  const done = useMemo(
    () => last !== null && (last.type === "run_completed" || last.type === "run_failed"),
    [last],
  );

  return { events, connected: !dropped, lastEventAt: last?.ts ?? null, done };
}
