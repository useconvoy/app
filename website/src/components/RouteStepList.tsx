import { stepStatusLabels } from "@/lexicon";

export type RouteStepState = "done" | "active" | "held" | "queued";

export interface RouteStep {
  id: string;
  /** Plain sentence, law 3: no ids, no tool names. */
  sentence: string;
  state: RouteStepState;
}

const stateLabels: Record<RouteStepState, string> = {
  done: stepStatusLabels.done ?? "Done",
  active: stepStatusLabels.active ?? "In progress",
  held: stepStatusLabels.blocked_on_human ?? "Held",
  queued: stepStatusLabels.queued ?? "Queued",
};

const nodeClasses: Record<RouteStepState, string> = {
  done: "border-pass bg-pass",
  active: "pulse-live border-pass bg-pass",
  held: "border-hold bg-hold",
  queued: "border-line bg-card",
};

const stateTextClasses: Record<RouteStepState, string> = {
  done: "text-pass",
  active: "text-pass",
  held: "text-hold",
  queued: "text-muted",
};

export interface RouteStepListProps {
  steps: RouteStep[];
}

/** The signature route motif: nodes on a connecting line, one per step. */
export function RouteStepList({ steps }: RouteStepListProps) {
  return (
    <ol className="m-0 list-none p-0">
      {steps.map((step, index) => (
        <li key={step.id} className="relative flex gap-3 pb-5 last:pb-0">
          {index < steps.length - 1 && (
            <span
              aria-hidden="true"
              className="absolute bottom-0 left-[5px] top-4 w-px bg-line"
            />
          )}
          <span
            aria-hidden="true"
            className={["mt-1.5 size-[11px] shrink-0 rounded-full border", nodeClasses[step.state]].join(" ")}
          />
          <span className="flex min-w-0 flex-col">
            <span className={step.state === "queued" ? "text-muted" : "text-ink"}>
              {step.sentence}
            </span>
            <span
              className={[
                "font-mono text-[11px] uppercase tracking-wide",
                stateTextClasses[step.state],
              ].join(" ")}
            >
              {stateLabels[step.state]}
            </span>
          </span>
        </li>
      ))}
    </ol>
  );
}
