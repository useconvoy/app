/**
 * Scored-run trajectories for a routine: a plain headline per run with a
 * friendly date. Rehearsal rows carry the pencil treatment (law 2):
 * graphite, dashed, and labeled; production rows are unlabeled. No run ids
 * in the list (law 3): the scorecards below carry the detail.
 */
import { Chip } from "@/components/Chip";
import type { Trajectory } from "@/lib/api/evals";
import { friendlyDateTime } from "@/lib/format";
import { terms } from "@/lexicon";

export interface TrajectoryListProps {
  items: Trajectory[];
}

export function TrajectoryList({ items }: TrajectoryListProps) {
  return (
    <ul className="m-0 list-none space-y-2 p-0">
      {items.map((item) => (
        <li
          key={item.runId}
          className={[
            "flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-4 py-3",
            item.sandbox ? "border-dashed border-graphite" : "border-line",
          ].join(" ")}
        >
          <span className={["text-sm", item.sandbox ? "text-graphite" : "text-ink"].join(" ")}>
            {item.headline}
          </span>
          <span className="flex items-center gap-2">
            {item.sandbox && (
              <Chip tone="graphite" dashed mono>
                {terms.sandbox}
              </Chip>
            )}
            <span className="font-mono text-xs uppercase text-muted">{friendlyDateTime(item.at)}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}
