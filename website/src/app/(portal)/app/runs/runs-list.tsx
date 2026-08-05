"use client";

/**
 * The runs list: filter chips, the
 * production/rehearsals toggle, search over goal text, and the four
 * columns run/status/progress/spent. Lists stay plain: no run ids, version
 * chips, or model names here; ids live only in the href. Filtering runs
 * client-side over server-fetched data (list sizes are small).
 */
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";

import { EmptyState } from "@/components/EmptyState";
import { StatusChip } from "@/components/StatusChip";
import { copy, runFilterLabels } from "@/lexicon";
import { money } from "@/lib/format";
import { filterGroup, type RunFilterGroup } from "@/lib/runs/status";

export interface RunListRow {
  /** Used for the link href only; never rendered in the list. */
  id: string;
  goal: string;
  status: string;
  done: number;
  total: number;
  spentUsd: string | null;
  rehearsal: boolean;
}

type GroupFilter = "all" | RunFilterGroup;
type ContextFilter = "production" | "rehearsals" | null;

const GROUPS: GroupFilter[] = ["all", "held", "running", "failed", "landed"];

function chipClass(active: boolean, rehearsalChip = false): string {
  const base = "rounded-full border px-3 py-1 text-sm";
  if (!active) return `${base} border-line bg-card text-muted hover:text-ink`;
  if (rehearsalChip) return `${base} border-dashed border-graphite bg-graphite-soft text-graphite`;
  return `${base} border-pine bg-pine text-card`;
}

export function RunsList({ rows, startAction }: { rows: RunListRow[]; startAction?: ReactNode }) {
  const [group, setGroup] = useState<GroupFilter>("all");
  const [context, setContext] = useState<ContextFilter>(null);
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return rows.filter((row) => {
      if (group !== "all" && filterGroup(row.status) !== group) return false;
      if (context === "production" && row.rehearsal) return false;
      if (context === "rehearsals" && !row.rehearsal) return false;
      if (needle && !row.goal.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [rows, group, context, query]);

  if (rows.length === 0) {
    return <EmptyState title={copy.noRunsYet} body={copy.runsEmptyBody} action={startAction} />;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Filter by status" className="flex flex-wrap gap-1.5">
          {GROUPS.map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={group === value}
              onClick={() => setGroup(value)}
              className={chipClass(group === value)}
            >
              {runFilterLabels[value]}
            </button>
          ))}
        </div>
        <span aria-hidden="true" className="mx-1 h-5 w-px bg-line" />
        <div role="group" aria-label="Filter by context" className="flex gap-1.5">
          <button
            type="button"
            aria-pressed={context === "production"}
            onClick={() => setContext(context === "production" ? null : "production")}
            className={chipClass(context === "production")}
          >
            {copy.productionFilter}
          </button>
          <button
            type="button"
            aria-pressed={context === "rehearsals"}
            onClick={() => setContext(context === "rehearsals" ? null : "rehearsals")}
            className={chipClass(context === "rehearsals", true)}
          >
            {copy.rehearsalsFilter}
          </button>
        </div>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder={copy.searchRuns}
          aria-label={copy.searchRuns}
          className="ml-auto w-56 rounded-md border border-line bg-card px-3 py-1.5 text-sm text-ink placeholder:text-muted"
        />
      </div>

      {visible.length === 0 ? (
        <EmptyState title="No runs match" body="Loosen the filters or clear the search." />
      ) : (
        <table className="w-full border-separate border-spacing-0 rounded-lg border border-line bg-card text-sm">
          <thead>
            <tr className="text-left font-mono text-xs uppercase tracking-wide text-muted">
              <th className="border-b border-line px-4 py-2 font-medium">Run</th>
              <th className="border-b border-line px-4 py-2 font-medium">Status</th>
              <th className="border-b border-line px-4 py-2 font-medium">Progress</th>
              <th className="border-b border-line px-4 py-2 font-medium">Spent</th>
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={row.id} data-rehearsal={row.rehearsal || undefined}>
                <td
                  className={[
                    "border-b border-line-soft px-4 py-3",
                    row.rehearsal ? "border-l-2 border-l-graphite [border-left-style:dashed]" : "",
                  ].join(" ")}
                >
                  <Link href={`/app/runs/${row.id}`} className="text-ink underline-offset-2 hover:underline">
                    {row.goal}
                  </Link>
                </td>
                <td className="border-b border-line-soft px-4 py-3">
                  <StatusChip status={row.status} rehearsal={row.rehearsal} />
                </td>
                <td className="border-b border-line-soft px-4 py-3 font-mono text-xs text-ink">
                  {row.done}/{row.total}
                </td>
                <td className="border-b border-line-soft px-4 py-3 font-mono text-xs text-ink">
                  {money(row.spentUsd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
