import { copy } from "@/lexicon";

/**
 * Overview v1: the calm state. Each section keeps its own seam so the W1
 * packet can swap the quiet placeholder for real data without reshaping
 * the page.
 */
export default function OverviewPage() {
  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-10">
      <header>
        <h1 className="font-display text-3xl text-ink">Overview</h1>
        <p className="mt-2 text-muted">{copy.allQuiet}</p>
      </header>
      <HeldForYou />
      <RunsInFlight />
      <RecentReports />
    </div>
  );
}

// TODO(website-W1): stuck-run data from the runs list replaces this.
function HeldForYou() {
  return <QuietSection title="Held for you" message="Nothing is waiting on your judgment." />;
}

// TODO(website-W1): live run data replaces this.
function RunsInFlight() {
  return <QuietSection title="Runs in flight" message="No runs are in flight right now." />;
}

// TODO(website-W1): recent land reports replace this.
function RecentReports() {
  return <QuietSection title="Recent reports" message="Reports will appear here after runs land." />;
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
