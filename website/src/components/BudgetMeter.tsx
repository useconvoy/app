import type { BudgetView } from "@/lib/api/client";
import { copy, eventLabels, runStatusLabels } from "@/lexicon";
import { money } from "@/lib/format";

export interface BudgetMeterProps {
  budget: BudgetView;
}

function toAmount(value: string | null | undefined): number {
  const amount = Number(value ?? 0);
  return Number.isFinite(amount) ? amount : 0;
}

/**
 * Cap / spent / reserved. Reserved renders as a hatched middle segment
 * labeled "set aside". Crossing 80% is an explicit hold-colored moment;
 * breach is fail-colored. Never color alone: each moment carries its label.
 */
export function BudgetMeter({ budget }: BudgetMeterProps) {
  const cap = toAmount(budget.cap_usd);
  const spent = toAmount(budget.spent_usd);
  const reserved = toAmount(budget.reserved_usd);

  const breached = cap > 0 && spent >= cap;
  const warning = !breached && cap > 0 && spent / cap >= 0.8;

  const spentPct = cap > 0 ? Math.min((spent / cap) * 100, 100) : 0;
  const reservedPct = cap > 0 ? Math.min((reserved / cap) * 100, 100 - spentPct) : 0;

  const fillColorVar = breached
    ? "var(--color-fail)"
    : warning
      ? "var(--color-hold)"
      : "var(--color-pine)";

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2 font-mono text-xs text-ink">
        <span>
          {money(spent)} of {money(cap)}
        </span>
        {reserved > 0 && (
          <span className="text-muted" data-testid="budget-reserved">
            {money(reserved)} {copy.setAside}
          </span>
        )}
      </div>
      <div
        role="img"
        aria-label={`${money(spent)} spent of ${money(cap)}, ${money(reserved)} ${copy.setAside}`}
        className="mt-1.5 flex h-2 overflow-hidden rounded-full bg-line-soft"
      >
        <span style={{ width: `${spentPct}%`, background: fillColorVar }} />
        <span
          data-testid="budget-reserved-segment"
          style={{
            width: `${reservedPct}%`,
            backgroundImage: `repeating-linear-gradient(135deg, ${fillColorVar} 0px, ${fillColorVar} 3px, transparent 3px, transparent 6px)`,
          }}
        />
      </div>
      {breached && (
        <p className="mt-1.5 text-sm font-medium text-fail">
          {runStatusLabels.budget_exhausted ?? "Out of budget"}
        </p>
      )}
      {warning && (
        <p className="mt-1.5 text-sm font-medium text-hold">
          {eventLabels.budget_warning ?? "Approaching budget"}
        </p>
      )}
    </div>
  );
}
