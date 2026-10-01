"use client";

import { Icon } from "./Icons";

export type StepState = "done" | "current" | "upcoming";
export interface StepItem { id: string; label: string; value?: string; state: StepState }

/** Vertical stepper (done ✓ / current / upcoming); every step is a button that selects it. */
export function Stepper({ steps, onSelect, label }: { steps: readonly StepItem[]; onSelect?: (id: string) => void; label: string }) {
  return <ol className="cfg-stepper" aria-label={label}>
    {steps.map((step, i) => <li key={step.id} className={`cfg-step cfg-step--${step.state}`}>
      <button className="cfg-step__btn" type="button" aria-current={step.state === "current" ? "step" : undefined} onClick={() => onSelect?.(step.id)}>
        <span className="cfg-step__mark"><Icon name="check" /><span className="cfg-step__n">{String(i + 1).padStart(2, "0")}</span></span>
        <span><span className="cfg-step__label">{step.label}</span>{step.value && <span className="cfg-step__value">{step.value}</span>}</span>
      </button>
    </li>)}
  </ol>;
}
