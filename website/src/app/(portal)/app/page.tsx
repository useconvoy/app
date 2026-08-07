import Link from "next/link";

import { StatusChip } from "@/components/StatusChip";
import { checkpointKinds, copy } from "@/lexicon";
import type { RunView } from "@/lib/api/client";
import { listRuns } from "@/lib/api/runs";
import { money } from "@/lib/format";
import { requireRunPage } from "@/lib/runs/context";
import { filterGroup, heldKind, progress, type HeldKind } from "@/lib/runs/status";
import { isRehearsalRun } from "@/lib/routines/data";

/**
 * Overview v1: held-for-you, runs in flight, recent reports; the calm
 * "all quiet" header when nothing needs anyone. Server components over
 * live control-plane reads; nothing here is cached into tables.
 */
export default async function OverviewPage() {
  const { actor } = await requireRunPage();
  let runs: RunView[] = [];
  try {
    runs = await listRuns(actor);
  } catch {
    // The control plane may be unreachable; the calm state stands in.
    runs = [];
  }

  const held = runs.filter((run) => heldKind(run.status) !== null);
  const inFlight = runs.filter((run) => filterGroup(run.status) === "running");
  const reports = runs.filter(
    (run) => filterGroup(run.status) === "landed" && run.land_report != null,
  );
  const allQuiet = held.length === 0 && inFlight.length === 0;

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-10">
      <header>
        <h1 className="font-display text-3xl text-ink">Overview</h1>
        {allQuiet && <p className="mt-2 text-muted">{copy.allQuiet}</p>}
      </header>
      <HeldForYou runs={held} />
      <RunsInFlight runs={inFlight} />
      <RecentReports runs={reports} />
    </div>
  );
}

/** Stuck runs, one typed line per stuck kind, each linking to its run. */
function HeldForYou({ runs }: { runs: RunView[] }) {
  if (runs.length === 0) {
    return <QuietSection title="Held for you" message="Nothing is waiting on your judgment." />;
  }
  return (
    <RunSection title="Held for you">
      {runs.map((run) => {
        const kind = heldKind(run.status) as HeldKind;
        return (
          <li key={run.run_id}>
            <RunLine run={run}>
              <span className="text-sm text-hold-text">{checkpointKinds[kind].description}</span>
            </RunLine>
          </li>
        );
      })}
    </RunSection>
  );
}

function RunsInFlight({ runs }: { runs: RunView[] }) {
  if (runs.length === 0) {
    return <QuietSection title="Runs in flight" message="No runs are in flight right now." />;
  }
  return (
    <RunSection title="Runs in flight">
      {runs.map((run) => {
        const { done, total } = progress(run.steps ?? []);
        return (
          <li key={run.run_id}>
            <RunLine run={run}>
              <span className="font-mono text-xs text-muted">
                {done}/{total} steps
              </span>
            </RunLine>
          </li>
        );
      })}
    </RunSection>
  );
}

function RecentReports({ runs }: { runs: RunView[] }) {
  if (runs.length === 0) {
    return (
      <QuietSection title="Recent reports" message="Reports will appear here after runs land." />
    );
  }
  return (
    <RunSection title="Recent reports">
      {runs.map((run) => (
        <li key={run.run_id}>
          <RunLine run={run}>
            <span className="font-mono text-xs text-muted">{money(run.budget?.spent_usd)} spent</span>
          </RunLine>
        </li>
      ))}
    </RunSection>
  );
}

function RunLine({ run, children }: { run: RunView; children?: React.ReactNode }) {
  const rehearsal = isRehearsalRun(run.run_id);
  return (
    <Link
      href={`/app/runs/${run.run_id}`}
      className={[
        "flex flex-wrap items-center gap-3 rounded-md border bg-card px-4 py-3 hover:bg-field",
        rehearsal ? "border-dashed border-graphite" : "border-line-soft",
      ].join(" ")}
    >
      <span className="min-w-0 flex-1 truncate text-sm text-ink">{run.goal}</span>
      {children}
      <StatusChip status={run.status} rehearsal={rehearsal} />
    </Link>
  );
}

function RunSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title}>
      <h2 className="font-display text-lg text-ink">{title}</h2>
      <ul className="mt-3 flex list-none flex-col gap-2 p-0">{children}</ul>
    </section>
  );
}

function QuietSection({ title, message }: { title: string; message: string }) {
  return (
    <section aria-label={title}>
      <h2 className="font-display text-lg text-ink">{title}</h2>
      <div className="mt-3 rounded-lg border border-line-soft bg-card px-6 py-8 text-center">
        <p className="text-sm text-muted">{message}</p>
      </div>
    </section>
  );
}
