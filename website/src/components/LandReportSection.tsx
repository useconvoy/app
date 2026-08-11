import type { ReactNode } from "react";

import { copy } from "@/lexicon";
import type { BudgetView } from "@/lib/api/client";
import { money } from "@/lib/format";
import type { RunStreamEvent } from "@/lib/runs/status";
import {
  deriveStandInOutbox,
  landReportFacts,
  type OutboxStep,
} from "@/lib/runs/land-report";

import { BudgetMeter } from "./BudgetMeter";

export interface LandReportSectionProps {
  report: Record<string, unknown>;
  budget: BudgetView | null;
  /** RunView steps, for deriving the stand-in outbox rows. */
  steps: readonly OutboxStep[];
  /** Recorded connector stand-in effects from the run's event stream. */
  events?: readonly RunStreamEvent[];
  /** Rehearsal reports carry the stand-in outbox. */
  rehearsal: boolean;
  /** Action area: export link, promotion submit, or the promote decision. */
  children?: ReactNode;
}

/**
 * The land report: outcome in plain language, steps, exceptions,
 * spend vs cap, files, and, on rehearsals, the stand-in outbox table of
 * everything the routine would have done.
 */
export function LandReportSection({
  report,
  budget,
  steps,
  events = [],
  rehearsal,
  children,
}: LandReportSectionProps) {
  const facts = landReportFacts(report);
  const landed = facts.status === "completed" || facts.status === "landed";
  const outbox = rehearsal ? deriveStandInOutbox(report, steps, events) : [];
  const exceptions = facts.stepsFailed + facts.stepsSkipped;

  return (
    <section
      aria-label={copy.landReportTitle}
      className={[
        "rounded-lg border bg-card p-5",
        rehearsal ? "border-dashed border-graphite" : "border-line",
      ].join(" ")}
    >
      <h2 className="font-display text-lg text-ink">{copy.landReportTitle}</h2>

      <dl className="mt-4 space-y-4 text-sm">
        <div>
          <dt className="text-muted">{copy.outcomeTitle}</dt>
          <dd className="mt-1 text-ink">
            {landed ? copy.outcomeLanded(facts.goal) : copy.outcomeNotLanded(facts.goal)}
          </dd>
        </div>
        <div>
          <dt className="text-muted">Steps</dt>
          <dd className="mt-1 font-mono text-xs text-ink">
            {copy.stepsSummary(facts.stepsDone, facts.stepsFailed, facts.stepsSkipped)}
          </dd>
        </div>
        <div>
          <dt className="text-muted">{copy.exceptionsTitle}</dt>
          <dd className="mt-1 text-sm">
            {exceptions === 0 ? (
              <span className="text-muted">{copy.noExceptions}</span>
            ) : (
              <span className="text-hold-text">
                {copy.stepsSummary(facts.stepsDone, facts.stepsFailed, facts.stepsSkipped)}
              </span>
            )}
          </dd>
        </div>
        {budget && (
          <div>
            <dt className="text-muted">{copy.spendVsCapTitle}</dt>
            <dd className="mt-1">
              <BudgetMeter budget={budget} />
            </dd>
          </div>
        )}
        <div>
          <dt className="text-muted">{copy.filesTitle}</dt>
          <dd className="mt-1">
            {facts.files.length === 0 ? (
              <span className="text-muted">None</span>
            ) : (
              <ul className="m-0 list-none space-y-1 p-0">
                {facts.files.map((file) => (
                  <li key={file.name} className="font-mono text-xs text-ink">
                    {file.name}
                    {file.sizeBytes !== null && (
                      <span className="text-muted"> · {file.sizeBytes} bytes</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>
      </dl>

      {rehearsal && (
        <div className="mt-6">
          <h3 className="text-base font-medium text-ink">{copy.standInOutbox}</h3>
          <p className="mt-1 text-sm text-muted">{copy.outboxDerivedNote}</p>
          {outbox.length === 0 ? (
            <p className="mt-2 text-sm text-muted">{copy.noExceptions}</p>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b border-line text-left text-muted">
                    <th scope="col" className="py-2 pr-4 font-normal">
                      {copy.outboxWhat}
                    </th>
                    <th scope="col" className="py-2 pr-4 font-normal">
                      {copy.outboxWhere}
                    </th>
                    <th scope="col" className="py-2 font-normal">
                      {copy.outboxContent}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {outbox.map((row, index) => (
                    <tr key={index} className="border-b border-line-soft last:border-b-0">
                      <td className="py-2 pr-4 text-ink">{row.what}</td>
                      <td className="py-2 pr-4 text-muted">{row.where}</td>
                      <td className="py-2 font-mono text-xs text-ink">{row.content}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {facts.costUsd !== null && (
        <p className="mt-4 font-mono text-xs text-muted">{money(facts.costUsd)} spent in total</p>
      )}

      {children && <div className="mt-5 border-t border-line-soft pt-4">{children}</div>}
    </section>
  );
}
