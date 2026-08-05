export type StepState = "done" | "held" | "queued";

export interface Step {
  label: string;
  state: StepState;
  /** Optional plain-language aside rendered under the step. */
  note?: string;
}

const NODE_CLASSES: Record<StepState, string> = {
  done: "border-pass bg-pass-soft",
  held: "border-hold bg-hold-soft",
  queued: "border-line bg-card",
};

const STATE_LABELS: Record<StepState, string> = {
  done: "Done",
  held: "Held for review",
  queued: "Queued",
};

/**
 * The route motif: steps as plain sentences with status nodes. Status is
 * always labeled in text, never carried by color alone.
 */
export function StepList({ steps }: { steps: Step[] }) {
  return (
    <ol className="space-y-0">
      {steps.map((step, index) => {
        const isLast = index === steps.length - 1;
        return (
          <li key={step.label} className="relative flex gap-4 pb-6 last:pb-0">
            {!isLast && (
              <span
                aria-hidden="true"
                className="absolute top-5 left-[9px] h-full w-px bg-line"
              />
            )}
            <span
              aria-hidden="true"
              className={`relative mt-1 inline-block h-[19px] w-[19px] shrink-0 rounded-full border ${NODE_CLASSES[step.state]}`}
            />
            <div>
              <p className="text-ink">
                {step.label}
                <span className="ml-2 font-mono text-xs tracking-wide text-muted uppercase">
                  {STATE_LABELS[step.state]}
                </span>
              </p>
              {step.note && (
                <p
                  className={`mt-2 max-w-md rounded-md border px-3 py-2 text-sm ${
                    step.state === "held"
                      ? "border-hold bg-hold-soft text-ink"
                      : "border-line-soft bg-card text-muted"
                  }`}
                >
                  {step.note}
                </p>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
