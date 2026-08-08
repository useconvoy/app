import type { Metadata } from "next";

import { Button } from "@/components/Button";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
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
 * Logs v1, honest about what the runtime exposes today: a cross-run
 * event explorer over real projections plus cost rollups. Viewer role is
 * redirected server-side; Admins, Operators, and Members may look.
 * Events fan in per run until the runtime's org feed exists.
 * TODO(runtime-D8).
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

      {/* Filters stack full width on phones and sit in a row from md up. */}
      <form method="get" className="flex flex-col gap-3 md:flex-row md:flex-wrap md:items-end">
        <Field label={logsCopy.filterEventType}>
          <Select id="filter-type" name="type" defaultValue={filters.type}>
            <option value="">{logsCopy.allEvents}</option>
            {Object.entries(eventLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={logsCopy.filterRun}>
          <Select
            id="filter-run"
            name="run"
            defaultValue={filters.run}
            className="md:max-w-72"
          >
            <option value="">{logsCopy.allRuns}</option>
            {runs.map((run) => (
              <option key={run.run_id} value={run.run_id}>
                {run.goal}
              </option>
            ))}
          </Select>
        </Field>
        <Field label={logsCopy.filterLens}>
          <Select id="filter-lens" name="lens" defaultValue={filters.lens}>
            <option value="">{logsCopy.allLenses}</option>
            <option value="production">{copy.productionFilter}</option>
            <option value="rehearsals">{copy.rehearsalsFilter}</option>
          </Select>
        </Field>
        <Button type="submit" variant="secondary" className="max-md:w-full">
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
