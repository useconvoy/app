import { eventLabels } from "@/lexicon";
import { friendlyDateTime } from "@/lib/format";

export interface TimelineRowProps {
  /** Runtime event type; the label always renders through the lexicon. */
  event: string;
  /** The acting human or the routine itself; every row is attributed. */
  actor: string;
  /** Real time of the event. */
  at: string | Date;
  /**
   * Rehearsal virtual time. When present it renders as the primary stamp
   * and real time drops to secondary.
   */
  virtualAt?: string | Date;
  /** Expandable raw detail; a quiet operator affordance, mono. */
  payload?: unknown;
}

export function TimelineRow({ event, actor, at, virtualAt, payload }: TimelineRowProps) {
  const label = eventLabels[event] ?? event.replaceAll("_", " ");

  // Compaction is bookkeeping, not narrative: one quiet collapsed line.
  if (event === "compaction_applied") {
    return (
      <li className="flex items-baseline gap-3 py-1 text-xs text-muted">
        <span className="font-mono">{friendlyDateTime(virtualAt ?? at)}</span>
        <span>{label}</span>
        <span>{actor}</span>
      </li>
    );
  }

  return (
    <li className="flex gap-3 border-b border-line-soft py-2 last:border-b-0">
      <span className="flex shrink-0 flex-col font-mono text-xs">
        <span className="text-ink">{friendlyDateTime(virtualAt ?? at)}</span>
        {virtualAt && <span className="text-muted">{friendlyDateTime(at)}</span>}
      </span>
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="text-sm text-ink">{label}</span>
        <span className="text-xs text-muted">{actor}</span>
        {payload !== undefined && (
          <details className="mt-1">
            <summary className="cursor-pointer text-xs text-muted">Details</summary>
            <pre className="mt-1 overflow-x-auto rounded-md bg-field p-2 font-mono text-xs text-ink">
              {JSON.stringify(payload, null, 2)}
            </pre>
          </details>
        )}
      </span>
    </li>
  );
}
