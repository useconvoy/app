/**
 * The cross-run event table for Logs: friendly time in mono, the event in
 * plain language through the lexicon, the acting identity, and the run's
 * goal as the link. Raw payloads are operator detail and live behind an
 * expandable row, never in the main columns (law 3). Rehearsal events are
 * labeled (law 2); production rows carry no tag.
 */
import Link from "next/link";

import { Chip } from "@/components/Chip";
import { EmptyState } from "@/components/EmptyState";
import type { LogEvent } from "@/lib/logs/events";
import { friendlyDateTime } from "@/lib/format";
import { eventLabels, logsCopy, terms } from "@/lexicon";

export interface LogEventTableProps {
  events: LogEvent[];
  /** Run goal per run id, for the run column's plain-language link. */
  runGoals: Record<string, string>;
}

export function LogEventTable({ events, runGoals }: LogEventTableProps) {
  if (events.length === 0) {
    return <EmptyState title={logsCopy.emptyTitle} body={logsCopy.emptyBody} />;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-card">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="text-left text-xs font-medium uppercase tracking-wide text-muted">
            <th scope="col" className="px-4 py-2.5 font-medium">
              {logsCopy.timeColumn}
            </th>
            <th scope="col" className="px-4 py-2.5 font-medium">
              {logsCopy.eventColumn}
            </th>
            <th scope="col" className="px-4 py-2.5 font-medium">
              {logsCopy.actorColumn}
            </th>
            <th scope="col" className="px-4 py-2.5 font-medium">
              {logsCopy.runColumn}
            </th>
          </tr>
        </thead>
        <tbody>
          {events.map((event) => (
            <tr
              key={event.id}
              className={[
                "border-t align-top",
                event.sandbox ? "border-dashed border-graphite" : "border-line-soft",
              ].join(" ")}
            >
              <td className="whitespace-nowrap px-4 py-2.5 font-mono text-xs uppercase text-muted">
                {/* Virtual time is primary for rehearsal events (law 2). */}
                {friendlyDateTime(event.virtual_ts ?? event.ts)}
              </td>
              <td className="px-4 py-2.5">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-ink">
                    {eventLabels[event.type] ?? event.type.replaceAll("_", " ")}
                  </span>
                  {event.sandbox && (
                    <Chip tone="graphite" dashed mono>
                      {terms.sandbox}
                    </Chip>
                  )}
                </span>
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-muted">
                    {logsCopy.payloadSummary}
                  </summary>
                  <pre className="mt-1 max-w-xl overflow-x-auto rounded-md bg-field p-2 font-mono text-xs text-graphite">
                    {JSON.stringify(event.payload, null, 2)}
                  </pre>
                </details>
              </td>
              <td className="px-4 py-2.5 text-muted">{event.actor}</td>
              <td className="px-4 py-2.5">
                <Link href={`/app/runs/${event.run_id}`} className="text-ink underline">
                  {runGoals[event.run_id] ?? "View run"}
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
