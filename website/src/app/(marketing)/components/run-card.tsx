import { RouteMark } from "@/components/brand/route-mark";

/**
 * The signature artifact: one run, mid-flight, with a checkpoint holding it.
 *
 * This is the product demo. It is server rendered at its final size and never
 * animated as a whole, because it is the page's largest contentful paint and
 * an element that starts transparent is not counted as an LCP candidate until
 * it becomes visible.
 *
 * The nodes are small and read the way the mark reads: solid behind you, open
 * ahead of you. Glyphs inside a node this size would only be noise, so the
 * state is carried by the row's own label as well as its color, never by
 * color alone.
 *
 * The strand between two steps is a row of its own with something to say. A
 * list of five steps says the routine has five steps; a list of five steps
 * with the time and the output of each gap says the routine has been running
 * for two days, which is the claim the page is actually making.
 */
type StepState = "done" | "active" | "held" | "queued";

interface RunStep {
  title: string;
  detail: string;
  meta: string;
  state: StepState;
  /** What happened between this step and the next one. */
  gap?: string;
}

const STEPS: RunStep[] = [
  {
    title: "Collect user lists",
    detail: "Okta · Google Workspace · AWS IAM",
    meta: "34 FILES",
    state: "done",
    gap: "3 systems read · 12m",
  },
  {
    title: "Reconcile against the HR roster",
    detail: "Cross-checked 1,204 people",
    meta: "3 EXCEPTIONS",
    state: "done",
    gap: "1,204 records · 41m",
  },
  {
    title: "Draft exception memos",
    detail: "2 of 3 drafted, citing source records",
    meta: "IN PROGRESS",
    state: "active",
    gap: "running · 6m",
  },
  {
    title: "Checkpoint: your sign-off on memos",
    detail: "The run holds here until you respond",
    meta: "HELD FOR YOU",
    state: "held",
    gap: "waiting on you",
  },
  {
    title: "File evidence to the audit binder",
    detail: "Drive to workpaper index, checksummed",
    meta: "QUEUED",
    state: "queued",
  },
];

const NODE: Record<StepState, string> = {
  done: "bg-pass",
  active: "border-[2.5px] border-pass bg-card",
  held: "border-[2.5px] border-hold bg-hold-soft",
  queued: "border border-line bg-card",
};

const META: Record<StepState, string> = {
  done: "text-muted",
  active: "font-semibold text-pass-text",
  held: "font-semibold text-hold-text",
  queued: "text-muted opacity-60",
};

/* The strand leaving a step. Behind the front of the run it is a solid stroke
 * that has been walked; ahead of it, a dotted one that has not. */
const STRAND: Record<StepState, string> = {
  done: "border-l-2 border-pass opacity-45",
  active: "border-l-2 border-dotted border-pass opacity-60",
  held: "border-l-2 border-dotted border-hold",
  queued: "border-l border-dotted border-line",
};

export function RunCard() {
  return (
    <div className="relative">
      {/* The sheet underneath. A run is a document, and documents come in
          stacks. No shadow of its own: the offset is the whole effect. */}
      <span
        aria-hidden="true"
        className="absolute inset-0 -rotate-[1.1deg] rounded-[14px] border border-line bg-card"
      />

      {/* tick-corners: the four marks an auditor puts on a document under
          review. They cost one pseudo-element and they are the difference
          between a panel and an exhibit. */}
      <aside
        aria-label="An access review part way through a run"
        className="tick-corners relative rounded-[14px] border border-line bg-card shadow-[0_1px_0_rgba(24,36,32,.03),0_24px_44px_-34px_rgba(21,59,46,.32)]"
      >
        {/* Title bar. The instrument's own chrome, ruled off from its
            contents the way a report header is. */}
        <div className="flex items-start justify-between gap-3 border-b border-line-soft px-5 py-4">
          <div className="min-w-0">
            <p className="font-display text-[19px] leading-tight font-semibold text-ink">
              Q3 user access review
            </p>
            <p className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[10.5px] tracking-[0.1em] text-muted">
              <span className="tabular-nums">DAY 2 OF 4</span>
              {/* The stamp, off true by a degree so it reads as applied to the
                  page rather than set on it. */}
              <span className="inline-block -rotate-[1.2deg] rounded-[3px] border border-fail px-1.5 py-0.5 font-semibold text-fail">
                PLAN APPROVED · J. DOE
              </span>
            </p>
          </div>
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full bg-pass-soft px-2.5 py-1 font-mono text-[10.5px] font-semibold tracking-[0.09em] text-pass-text">
            <span className="pulse-live h-[6px] w-[6px] rounded-full bg-pass" />
            RUNNING
          </span>
        </div>

        <ol className="px-5 py-4">
          {STEPS.map((step) => (
            <li key={step.title} className="relative pl-[26px] last:pb-0">
              {/* The strand runs from under the node to the bottom of the row,
                  which is where the next node starts. The last step has no
                  next step, so it has no strand. */}
              {step.gap ? (
                <span
                  aria-hidden="true"
                  className={`absolute top-[17px] bottom-0 left-[5px] ${STRAND[step.state]}`}
                />
              ) : null}

              <span
                aria-hidden="true"
                className={`absolute top-1 left-0 h-[11px] w-[11px] rounded-full ${NODE[step.state]}`}
              >
                {step.state === "active" ? (
                  <span className="pulse-live absolute inset-[1px] rounded-full bg-pass" />
                ) : null}
              </span>

              <div
                className={`flex items-baseline justify-between gap-3 ${
                  step.state === "queued" ? "opacity-60" : ""
                }`}
              >
                <p className="text-[14.5px] leading-snug font-semibold tracking-[-0.005em] text-ink">
                  {step.title}
                </p>
                {/* Tabular figures on a shared baseline: the labels line up
                    down the right edge instead of drifting with their rows. */}
                <span
                  className={`shrink-0 font-mono text-[10.5px] tracking-[0.07em] whitespace-nowrap tabular-nums ${META[step.state]}`}
                >
                  {step.meta}
                </span>
              </div>
              <p
                className={`text-[12.5px] leading-snug text-muted ${
                  step.state === "queued" ? "opacity-60" : ""
                }`}
              >
                {step.detail}
              </p>

              {/* The gap. A row with its own height and its own fact. */}
              {step.gap ? (
                <p className="py-3 font-mono text-[10.5px] tracking-[0.07em] text-muted tabular-nums">
                  {step.gap}
                </p>
              ) : null}
            </li>
          ))}
        </ol>

        <div className="border-t border-line-soft px-5 py-4">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-[10.5px] tracking-[0.1em] text-muted">
              RUN BUDGET
            </span>
            <b className="font-mono text-[12px] font-semibold text-ink tabular-nums">
              $18.40 / $75.00 CAP
            </b>
          </div>
          {/* The cap is drawn, not implied: a hatched fill against a redline
              standing at the number it is not allowed to pass. */}
          <div className="relative mt-2 h-[7px] rounded-[2px] bg-line-soft">
            <span className="hatch-pass block h-full w-[24.5%] rounded-l-[2px]" />
            <span
              aria-hidden="true"
              className="absolute inset-y-[-3px] right-0 border-r-2 border-dashed border-fail"
            />
          </div>
          <p className="mt-3.5 flex items-center gap-2 font-mono text-[10.5px] tracking-[0.07em] text-muted">
            <RouteMark width={20} surface="card" />
            FIVE STEPS · ONE CHECKPOINT
          </p>
        </div>
      </aside>
    </div>
  );
}
