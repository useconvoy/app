import { EXECUTION } from "@/content/homepage";
import { Disclosure } from "./Disclosure";
import { ExecutionTrace } from "./ExecutionTrace";
import { Section, SectionHeading } from "./Section";

/**
 * The execution path in the design system's diagram grammar. Nodes are linen
 * with a meaningful border, the model node white with an ink border, and
 * anything outside Convoy (sensors, controller) dashed grey. Solid arrows
 * carry execution. The three Convoy-scoped nodes sit inside a labelled
 * dashed oxide boundary that is a real group in the accessibility tree and
 * keeps its label in the vertical layout below 900px; a visually hidden
 * sentence states the same relationships in words. Placement labels under
 * each node say where it may run, dotted where that is configuration
 * dependent. The figure itself, including its one-shot trace, is the
 * client component ExecutionTrace. Then the two questions (placement,
 * repeatability) and the release-boundary disclosure with the dark panel.
 */
export function ExecutionPath() {
  return (
    <Section id={EXECUTION.id} labelledBy="execution-heading">
      <SectionHeading id="execution-heading" eyebrow={EXECUTION.eyebrow} heading={EXECUTION.heading} lead={EXECUTION.lead} />

      <figure className="mt-10" aria-labelledby="execution-diagram-title" aria-describedby="execution-diagram-description execution-diagram-caption">
        <figcaption id="execution-diagram-title" className="sr-only">
          {EXECUTION.path}
        </figcaption>
        <p id="execution-diagram-description" className="sr-only">
          {EXECUTION.description}
        </p>
        <div className="mt-2">
          <ExecutionTrace />
        </div>
      </figure>

      <div className="mt-8 grid gap-x-8 gap-y-2 md:grid-cols-2">
        <Disclosure summary={EXECUTION.placement.question} className="border-t border-strong">
          <p className="type-body">{EXECUTION.placement.answer}</p>
          <ul className="mt-4 grid gap-2" aria-label="Deployment placement options">
            {EXECUTION.placement.options.map((option) => (
              <li key={option.label} className="flex flex-wrap items-baseline gap-x-3 border-t border-subtle py-2">
                <span className="type-label text-primary">{option.label}</span>
                <span className="type-small text-secondary">{option.note}</span>
              </li>
            ))}
          </ul>
          <p className="type-small mt-3 text-secondary">{EXECUTION.placement.rule}</p>
        </Disclosure>

        <Disclosure summary={EXECUTION.repeatable.question} className="border-t border-strong">
          <p className="type-body">{EXECUTION.repeatable.answer}</p>
        </Disclosure>
      </div>

      <div className="mt-4 border-t border-strong">
        <Disclosure summary={EXECUTION.boundaryDisclosure.label} summaryClassName="type-h4 text-accent">
          <p className="type-body prose-measure">{EXECUTION.boundaryDisclosure.intro}</p>
          <ReleaseRecordPanel />
          <p className="type-small prose-measure mt-4 text-secondary">{EXECUTION.boundaryDisclosure.note}</p>
        </Disclosure>
      </div>
    </Section>
  );
}

/** The one dark surface: the conceptual categories a release carries, no values. */
function ReleaseRecordPanel() {
  const { panelLabel, panelStatus, panelFooter, groups } = EXECUTION.boundaryDisclosure;
  return (
    <div className="mt-5 rounded-lg bg-inverse text-inverse-text">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 border-b border-inverse-muted/30 px-4 py-3 font-mono text-[0.8125rem] uppercase tracking-[0.06em] text-inverse-muted">
        <span>{panelLabel}</span>
        <span>{panelStatus}</span>
      </div>
      <div className="px-4 py-4">
        <dl className="type-code grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[136px_minmax(0,1fr)]">
          {groups.flatMap((group) => [
            <dt key={group.title} className="mt-3 font-mono text-xs uppercase tracking-[0.06em] text-inverse-muted first:mt-0 sm:col-span-2">
              {group.title}
            </dt>,
            ...group.rows.flatMap((row) => [
              <dt key={`${group.title}-${row.key}`} className="text-code-keyword">
                {row.key}
              </dt>,
              <dd key={`${group.title}-${row.key}-value`} className="mb-1 text-code-string sm:mb-0">
                {row.value}
              </dd>,
            ]),
          ])}
        </dl>
      </div>
      <div className="border-t border-inverse-muted/30 px-4 py-3 font-mono text-[0.8125rem] tracking-[0.02em] text-inverse-muted">{panelFooter}</div>
    </div>
  );
}
