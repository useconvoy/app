import { EXECUTION } from "@/content/homepage";
import { Disclosure } from "./Disclosure";
import { Section, SectionHeading } from "./Section";

/**
 * The execution path in the design system's diagram grammar: linen nodes
 * with a meaningful border, the model node white with an ink border, and
 * anything outside Convoy (sensors, controller) dashed grey. Solid arrows
 * carry execution; the Convoy runtime scope is a dashed oxide bracket under
 * the middle nodes; placement labels under each node say where it may run,
 * dotted where that is configuration-dependent. Then the two questions
 * (placement, repeatability) and the release-boundary disclosure that opens
 * the page's one dark diagnostic panel. Nodes stack vertically below 900px.
 */
export function ExecutionPath() {
  return (
    <Section id={EXECUTION.id} labelledBy="execution-heading">
      <SectionHeading id="execution-heading" eyebrow={EXECUTION.eyebrow} heading={EXECUTION.heading} lead={EXECUTION.lead} />

      <figure className="mt-10" aria-labelledby="execution-diagram-title" aria-describedby="execution-diagram-text">
        <figcaption id="execution-diagram-title" className="type-label text-primary">
          {EXECUTION.path}
        </figcaption>
        <p id="execution-diagram-text" className="type-body prose-measure mt-2 text-secondary">
          {EXECUTION.boundary}
        </p>
        <div className="mt-8">
          <PathDiagram />
        </div>
      </figure>

      <div className="mt-12 grid gap-x-8 gap-y-2 md:grid-cols-2">
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

      <div className="mt-6 border-t border-strong">
        <Disclosure summary={EXECUTION.boundaryDisclosure.label} summaryClassName="type-h4 text-accent">
          <p className="type-body prose-measure">{EXECUTION.boundaryDisclosure.intro}</p>
          <ReleaseRecordPanel />
          <p className="type-small prose-measure mt-4 text-secondary">{EXECUTION.boundaryDisclosure.note}</p>
        </Disclosure>
      </div>
    </Section>
  );
}

function PathDiagram() {
  const nodes = EXECUTION.nodes;
  return (
    <div>
      <ol className="flex flex-col gap-2 md:flex-row md:items-stretch md:gap-0" aria-label="Execution path nodes">
        {nodes.map((node, index) => (
          <NodeAndEdge key={node.key} node={node} edge={EXECUTION.edges[index]} />
        ))}
      </ol>

      {/* The runtime scope bracket: dashed oxide under the three middle nodes, from 900px. */}
      <div aria-hidden="true" className="relative mt-3 hidden h-10 md:block">
        <div className="absolute top-0 right-[calc(20%+20px)] left-[calc(20%+20px)] h-3 rounded-b border-x-[1.5px] border-b-[1.5px] border-dashed border-accent">
          <span className="absolute top-3 left-1/2 -translate-x-1/2 whitespace-nowrap bg-background px-2 pt-1 font-mono text-[11px] tracking-[0.02em] text-accent">
            {EXECUTION.scope}
          </span>
        </div>
      </div>
      <p className="mt-3 font-mono text-[11px] tracking-[0.02em] text-accent md:hidden">{EXECUTION.scope}</p>

      {/* Feedback: new observations and robot state travel back to the sensors. */}
      <div className="mt-2 flex items-center gap-3 type-small text-secondary">
        <svg viewBox="0 0 120 20" className="h-5 w-[120px] flex-none text-strong" aria-hidden="true" focusable="false">
          <path d="M115 4v8H8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" />
          <path d="M13 8l-6 4 6 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>{EXECUTION.feedback}</span>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <span className="max-w-[60ch] font-mono text-[12px] tracking-[0.02em] text-muted">{EXECUTION.diagramLabel}</span>
        <ul className="flex flex-wrap gap-x-6 gap-y-2 text-[13px] text-secondary" aria-label="Diagram legend">
          {EXECUTION.legend.map((item) => (
            <li key={item.kind} className="inline-flex items-center gap-2">
              <i
                aria-hidden="true"
                className={`inline-block w-7 border-t-[1.5px] ${
                  item.kind === "execution" ? "border-solid border-primary" : item.kind === "release" ? "border-dashed border-accent" : "border-dotted border-muted"
                }`}
              />
              {item.label}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

function NodeAndEdge({ node, edge }: { node: (typeof EXECUTION.nodes)[number]; edge?: string }) {
  const external = node.owner === "robot";
  const model = "model" in node && node.model;
  return (
    <>
      <li className="flex min-w-0 flex-1 flex-col gap-2">
        <div
          className={`flex min-h-14 flex-1 flex-col items-center justify-center gap-[2px] rounded border px-4 py-3 text-center ${
            external
              ? "border-dashed border-strong bg-transparent text-secondary"
              : model
                ? "border-primary bg-surface text-primary"
                : "border-strong bg-surface-subtle text-primary"
          }`}
        >
          <span className="type-small font-medium">{node.label}</span>
          <span className="font-mono text-[11px] tracking-[0.02em] text-secondary">{node.note}</span>
        </div>
        <span
          className={`text-center font-mono text-[11px] tracking-[0.02em] ${
            node.optional ? "border-t-[1.5px] border-dotted border-muted pt-2 text-muted" : "text-muted"
          }`}
        >
          {node.site}
        </span>
      </li>
      {edge ? (
        <li aria-hidden="true" className="flex items-center gap-2 pl-4 md:h-14 md:w-10 md:flex-none md:flex-col md:justify-center md:gap-0 md:pl-0">
          <svg viewBox="0 0 40 16" className="h-4 w-10 rotate-90 text-primary md:rotate-0" focusable="false">
            <path d="M1 8h32" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
            <path d="M29 3.5 34.5 8 29 12.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          <span className="type-small whitespace-nowrap text-muted md:sr-only">{edge}</span>
        </li>
      ) : null}
    </>
  );
}

/** The one dark diagnostic surface: an illustrative release record, field by field. */
function ReleaseRecordPanel() {
  const { panelLabel, panelStatus, panelFooter, fields } = EXECUTION.boundaryDisclosure;
  return (
    <div className="mt-5 rounded-lg bg-inverse text-inverse-text">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 border-b border-inverse-muted/30 px-4 py-3 font-mono text-[12px] uppercase tracking-[0.06em] text-inverse-muted">
        <span>{panelLabel}</span>
        <span>{panelStatus}</span>
      </div>
      <div className="px-4 py-4">
        <dl className="type-code grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-[120px_minmax(0,1fr)] sm:gap-y-2">
          {fields.map((field) => (
            <div key={field.key} className="contents">
              <dt className="text-code-keyword">{field.key}</dt>
              <dd className="mb-1 sm:mb-0">
                <span className={field.key === "release" ? "text-code-value" : "text-code-string"}>{field.value}</span>
                {"note" in field && field.note ? <span className="text-inverse-muted"> · {field.note}</span> : null}
              </dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="border-t border-inverse-muted/30 px-4 py-3 font-mono text-[12px] tracking-[0.02em] text-inverse-muted">{panelFooter}</div>
    </div>
  );
}
