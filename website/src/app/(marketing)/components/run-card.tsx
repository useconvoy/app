import { RouteMark } from "@/components/brand/route-mark";

/**
 * The signature artifact: one run, mid-flight, with a checkpoint holding it.
 *
 * This is the product demo. It is server rendered at its final size and never
 * animated as a whole, because it is the page's largest contentful paint and
 * an element that starts transparent is not counted as an LCP candidate until
 * it becomes visible.
 *
 * Node states come from the design system: done is a filled node with a
 * check, active is an open node with the one pulse in the interface, held
 * carries the hold colors and a pause glyph, queued is a line-colored outline
 * with its content at 55%.
 */
type StepState = "done" | "active" | "held" | "queued";

interface RunStep {
  title: string;
  detail: string;
  meta: string;
  state: StepState;
}

const STEPS: RunStep[] = [
  {
    title: "Collect user lists",
    detail: "Okta · Google Workspace · AWS IAM",
    meta: "34 FILES",
    state: "done",
  },
  {
    title: "Reconcile against the HR roster",
    detail: "Cross-checked 1,204 people",
    meta: "3 EXCEPTIONS",
    state: "done",
  },
  {
    title: "Draft exception memos",
    detail: "2 of 3 drafted, citing source records",
    meta: "IN PROGRESS",
    state: "active",
  },
  {
    title: "Checkpoint: your sign-off on memos",
    detail: "The run holds here until you respond",
    meta: "HELD FOR YOU",
    state: "held",
  },
  {
    title: "File evidence to the audit binder",
    detail: "Drive to workpaper index, checksummed",
    meta: "QUEUED",
    state: "queued",
  },
];

function Check() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <path
        d="M2 6.2 5 9l5-6"
        className="stroke-card"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function Pause() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
      <rect x="4.2" y="2" width="1.6" height="8" rx="0.8" className="fill-hold" />
      <rect x="7.2" y="2" width="1.6" height="8" rx="0.8" className="fill-hold" />
    </svg>
  );
}

function Node({ state }: { state: StepState }) {
  const base =
    "relative z-10 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border-2";
  if (state === "done")
    return (
      <span className={`${base} border-pass bg-pass`}>
        <Check />
      </span>
    );
  if (state === "active")
    return (
      <span className={`${base} border-pass bg-card`}>
        <span className="pulse-live h-2.5 w-2.5 rounded-full bg-pass" />
      </span>
    );
  if (state === "held")
    return (
      <span className={`${base} border-hold bg-hold-soft`}>
        <Pause />
      </span>
    );
  return <span className={`${base} border-line bg-card`} />;
}

export function RunCard() {
  return (
    <aside
      aria-label="An access review part way through a run"
      className="rounded-[18px] border border-line bg-card p-5 shadow-[0_1px_0_rgba(24,36,32,.04),0_32px_64px_-40px_rgba(21,59,46,.35)] sm:p-6"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-mono text-[11.5px] tracking-[0.1em] text-muted">
            DAY 2 OF 4
          </p>
          <p className="mt-1.5 font-display text-xl font-semibold text-ink">
            Q3 user access review
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-2 rounded-full bg-pass-soft px-2.5 py-1.5 font-mono text-[11px] font-semibold tracking-[0.09em] text-pass-text">
          <span className="pulse-live h-[7px] w-[7px] rounded-full bg-pass" />
          RUNNING
        </span>
      </div>

      {/* The stamp. Rotated a degree off true so it reads as applied to the
          page rather than set on it. */}
      <span className="mt-2 mb-4 inline-block -rotate-[1.2deg] rounded-md border-[1.5px] border-fail px-2 py-1 font-mono text-[10.5px] font-semibold tracking-[0.12em] text-fail">
        PLAN APPROVED · J. DOE
      </span>

      <ol className="relative mb-5">
        {/* The connector the checkpoints sit on. */}
        <span
          aria-hidden="true"
          className="absolute top-3.5 bottom-3.5 left-[13px] w-0.5 rounded bg-line"
        />
        {STEPS.map((step) => (
          <li
            key={step.title}
            className={`relative grid grid-cols-[28px_1fr_auto] items-start gap-x-3.5 py-2.5 ${
              step.state === "held"
                ? "-mx-2.5 rounded-[10px] bg-hold-soft px-2.5"
                : ""
            }`}
          >
            <Node state={step.state} />
            <div className={step.state === "queued" ? "opacity-55" : undefined}>
              <p className="pt-0.5 text-[14.5px] leading-snug font-semibold text-ink">
                {step.title}
              </p>
              <p className="text-[13px] leading-snug text-muted">{step.detail}</p>
            </div>
            <span
              className={`pt-1.5 font-mono text-[11px] tracking-[0.06em] whitespace-nowrap ${
                step.state === "held"
                  ? "font-semibold text-hold-text"
                  : step.state === "queued"
                    ? "text-muted opacity-55"
                    : "text-muted"
              }`}
            >
              {step.meta}
            </span>
          </li>
        ))}
      </ol>

      <div className="border-t border-line-soft pt-4">
        <div className="flex items-baseline justify-between">
          <span className="font-mono text-[11px] tracking-[0.1em] text-muted">
            RUN BUDGET
          </span>
          {/* Tabular figures, so a changing number never shifts the row. */}
          <b className="font-mono text-[12.5px] font-semibold text-ink tabular-nums">
            $18.40 / $75.00 CAP
          </b>
        </div>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-line-soft">
          <span className="block h-full w-[24.5%] rounded-full bg-pass" />
        </div>
        <p className="mt-4 flex items-center gap-2 font-mono text-[11px] tracking-[0.06em] text-muted">
          <RouteMark width={22} surface="card" />
          FIVE STEPS · ONE CHECKPOINT
        </p>
      </div>
    </aside>
  );
}
