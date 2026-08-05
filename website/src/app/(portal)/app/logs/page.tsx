import type { Metadata } from "next";

import { Button } from "@/components/Button";
import { CostRollupsPanel } from "@/components/CostRollupsPanel";
import { LogEventTable } from "@/components/LogEventTable";
import { listRuns } from "@/lib/api/runs";
import type { RunView } from "@/lib/api/client";
import { loadOrgEvents, type LogEvent } from "@/lib/logs/events";
import { requireLogsPage } from "@/lib/logs/gate";
import { modelSpends, runSpends, totalSpend } from "@/lib/logs/rollups";
import { orgTenantId } from "@/lib/routines/queries";
import { copy, eventLabels, logsCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Logs" };
export const dynamic = "force-dynamic";

interface LogsFilters {
  type: string;
  run: string;
  lens: string;
}

function applyFilters(events: LogEvent[], filters: LogsFilters): LogEvent[] {
  return events.filter((event) => {
    if (filters.type && event.type !== filters.type) return false;
    if (filters.run && event.run_id !== filters.run) return false;
    if (filters.lens === "production" && event.sandbox) return false;
    if (filters.lens === "rehearsals" && !event.sandbox) return false;
    return true;
  });
}

/**
 * Logs v1 (DESIGN §5, the handoff's honest gap): a cross-run event
 * explorer over real projections plus cost rollups. Viewer role is
 * redirected server-side (lens A/O/M). Events fan in per run until the
 * runtime's org feed exists. TODO(runtime-D8).
 */
export default async function LogsPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { session } = await requireLogsPage();
  const params = await searchParams;
  const filters: LogsFilters = {
    type: typeof params.type === "string" ? params.type : "",
    run: typeof params.run === "string" ? params.run : "",
    lens: typeof params.lens === "string" ? params.lens : "",
  };

  const tenantId = await orgTenantId(session.orgId);
  const actor = { actorId: session.userId, tenantId };

  // The control plane may be unreachable in fixture-only setups; the page
  // stays up with an empty explorer rather than failing.
  let runs: RunView[] = [];
  let events: LogEvent[] = [];
  try {
    [runs, events] = await Promise.all([listRuns(actor), loadOrgEvents(actor)]);
  } catch {
    runs = [];
    events = [];
  }

  const runGoals = Object.fromEntries(runs.map((run) => [run.run_id, run.goal]));
  const filtered = applyFilters(events, filters);

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <header>
        <h1 className="font-display text-3xl text-ink">{logsCopy.title}</h1>
        <p className="mt-2 text-sm text-muted">{logsCopy.intro}</p>
      </header>

      <form method="get" className="flex flex-wrap items-end gap-3">
        <div>
          <label htmlFor="filter-type" className="block text-xs font-medium uppercase tracking-wide text-muted">
            {logsCopy.filterEventType}
          </label>
          <select
            id="filter-type"
            name="type"
            defaultValue={filters.type}
            className="mt-1 rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
          >
            <option value="">{logsCopy.allEvents}</option>
            {Object.entries(eventLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="filter-run" className="block text-xs font-medium uppercase tracking-wide text-muted">
            {logsCopy.filterRun}
          </label>
          <select
            id="filter-run"
            name="run"
            defaultValue={filters.run}
            className="mt-1 max-w-72 rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
          >
            <option value="">{logsCopy.allRuns}</option>
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.goal}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="filter-lens" className="block text-xs font-medium uppercase tracking-wide text-muted">
            {logsCopy.filterLens}
          </label>
          <select
            id="filter-lens"
            name="lens"
            defaultValue={filters.lens}
            className="mt-1 rounded-md border border-line bg-card px-2 py-1.5 text-sm text-ink"
          >
            <option value="">{logsCopy.allLenses}</option>
            <option value="production">{copy.productionFilter}</option>
            <option value="rehearsals">{copy.rehearsalsFilter}</option>
          </select>
        </div>
        <Button type="submit" variant="secondary">
          {logsCopy.apply}
        </Button>
      </form>

      <div className="grid gap-6 lg:grid-cols-[1fr_20rem]">
        <LogEventTable events={filtered} runGoals={runGoals} />
        <CostRollupsPanel
          runs={runSpends(runs)}
          models={modelSpends(runs)}
          totalUsd={totalSpend(runs)}
        />
      </div>
    </div>
  );
}
