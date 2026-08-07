/**
 * Renders the environments-layer CompatibilityReport during an install:
 * green checks for satisfied systems, a "needs a mapping" picker when
 * several connected systems could serve, "connect X first" prompts for
 * missing ones, and the portability line for vendor-specific tools.
 * Presentational; the install flow owns the confirm step.
 */
import type { CompatibilityReport } from "@/lib/api/environments";
import { Chip } from "@/components/Chip";
import { catalogCopy } from "@/lexicon";

export interface CompatibilityReportViewProps {
  report: CompatibilityReport;
  /** System id -> plain display name; unknown ids render as themselves. */
  systemNames: Record<string, string>;
}

export function CompatibilityReportView({ report, systemNames }: CompatibilityReportViewProps) {
  const name = (systemId: string) => systemNames[systemId] ?? systemId;
  const allGreen = report.mappingChoices.length === 0 && report.connectPrompts.length === 0;
  return (
    <section aria-label={catalogCopy.compatibilityTitle} className="space-y-4">
      <h2 className="text-base font-medium text-ink">{catalogCopy.compatibilityTitle}</h2>

      {allGreen ? <p className="text-sm text-pass">{catalogCopy.allGreen}</p> : null}

      {report.greenChecks.length > 0 ? (
        <ul className="m-0 list-none space-y-1 p-0">
          {report.greenChecks.map((systemId) => (
            <li key={systemId} className="flex items-center gap-2 text-sm text-ink">
              <span aria-hidden className="text-pass">
                ✓
              </span>
              {catalogCopy.connectedCheck(name(systemId))}
            </li>
          ))}
        </ul>
      ) : null}

      {report.mappingChoices.map((choice) => (
        <div key={choice.systemId} className="rounded-md border border-line bg-field p-3">
          <label className="block text-sm text-ink">
            {catalogCopy.needsMapping(name(choice.systemId))}
            <select
              name={`mapping-${choice.systemId}`}
              defaultValue={choice.options[0]}
              className="mt-2 block rounded-sm border border-line bg-card px-3 py-2 text-sm"
            >
              {choice.options.map((option) => (
                <option key={option} value={option}>
                  {name(option)}
                </option>
              ))}
            </select>
          </label>
        </div>
      ))}

      {report.connectPrompts.length > 0 ? (
        <ul className="m-0 list-none space-y-1 p-0">
          {report.connectPrompts.map((systemId) => (
            <li key={systemId} className="flex items-center gap-2 text-sm text-hold-text">
              <Chip tone="hold" mono>
                Missing
              </Chip>
              {catalogCopy.connectFirst(name(systemId))}
            </li>
          ))}
        </ul>
      ) : null}

      <p className="font-mono text-xs uppercase text-muted">
        {report.vendorSpecificToolCount > 0
          ? catalogCopy.portableExcept(report.vendorSpecificToolCount)
          : catalogCopy.fullyPortable}
      </p>
    </section>
  );
}
