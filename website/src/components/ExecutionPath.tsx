import { EXECUTION } from "@/content/homepage";
import { Disclosure } from "./Disclosure";
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
 * dependent. Then the two questions (placement, repeatability) and the
 * release-boundary disclosure with the page's one dark panel.
 */
export function ExecutionPath() {
  return (
    <Section id={EXECUTION.id} labelledBy="execution-heading">
      <SectionHeading id="execution-heading" eyebrow={EXECUTION.eyebrow} heading={EXECUTION.heading} lead={EXECUTION.lead} />

      <figure className="mt-10" aria-labelledby="execution-diagram-title" aria-describedby="execution-diagram-description execution-diagram-caption">
        <figcaption id="execution-diagram-title" className="type-label text-primary">
          {EXECUTION.path}
        </figcaption>
        <p className="type-body prose-measure mt-2 text-secondary">{EXECUTION.boundary}</p>
        <p id="execution-diagram-description" className="sr-only">
          {EXECUTION.description}
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

type Node = (typeof EXECUTION.nodes)[number];

/** Consecutive nodes with the same owner form one run; the Convoy run is the labelled boundary. */
function runs(): { owner: Node["owner"]; nodes: Node[] }[] {
  const out: { owner: Node["owner"]; nodes: Node[] }[] = [];
  for (const node of EXECUTION.nodes) {
    const last = out[out.length - 1];
    if (last && last.owner === node.owner) last.nodes.push(node);
    else out.push({ owner: node.owner, nodes: [node] });
  }
  return out;
}

function PathDiagram() {
  const groups = runs();
  let edgeIndex = 0;
  return (
    <div>
      <div className="flex flex-col gap-2 md:flex-row md:items-stretch md:gap-0" data-execution-path>
        {groups.map((group, groupIndex) => {
          const convoy = group.owner === "convoy";
          const body = group.nodes.map((node, nodeIndex) => {
            const edge = nodeIndex < group.nodes.length - 1 ? EXECUTION.edges[edgeIndex++] : undefined;
            return <NodeAndEdge key={node.key} node={node} edge={edge} />;
          });
          const between = groupIndex < groups.length - 1 ? <Edge key={`edge-${groupIndex}`} label={EXECUTION.edges[edgeIndex++]} /> : null;
          return (
            <div key={group.owner + groupIndex} className="contents">
              {convoy ? (
                <div
                  role="group"
                  aria-label={EXECUTION.scope}
                  data-runtime-boundary
                  className="relative my-3 flex flex-col gap-2 rounded-lg border-2 border-dashed border-accent px-3 pt-8 pb-3 md:my-0 md:mx-1 md:flex-[3_1_auto] md:flex-row md:items-stretch md:gap-0"
                >
                  <span className="absolute -top-[11px] left-3 bg-background px-2 font-mono text-[14px] font-medium tracking-[0.02em] text-accent">
                    {EXECUTION.scope}
                  </span>
                  {body}
                </div>
              ) : (
                <div className="flex flex-col gap-2 md:flex-[1_1_auto] md:flex-row md:items-stretch md:gap-0 md:pt-8">{body}</div>
              )}
              {between}
            </div>
          );
        })}
      </div>

      {/* Feedback: new observations and robot state travel back to the sensors. */}
      <div className="mt-4 flex items-center gap-3 type-small text-secondary">
        <svg viewBox="0 0 120 20" className="h-5 w-[120px] flex-none text-strong" aria-hidden="true" focusable="false">
          <path d="M115 4v8H8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" />
          <path d="M13 8l-6 4 6 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>{EXECUTION.feedback}</span>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <span id="execution-diagram-caption" className="type-caption max-w-[60ch] text-secondary">
          {EXECUTION.caption}
        </span>
        <ul className="flex flex-wrap gap-x-6 gap-y-2 text-[14px] text-secondary" aria-label="Diagram legend">
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

function NodeAndEdge({ node, edge }: { node: Node; edge?: string }) {
  const external = node.owner === "robot";
  const model = "model" in node && node.model;
  return (
    <>
      <div className="flex min-w-0 flex-1 flex-col gap-2" data-node={node.key}>
        <div
          className={`flex min-h-16 flex-1 flex-col items-center justify-center gap-[2px] rounded border px-4 py-3 text-center ${
            external
              ? "border-dashed border-strong bg-transparent text-primary"
              : model
                ? "border-primary bg-surface text-primary"
                : "border-strong bg-surface-subtle text-primary"
          }`}
        >
          <span className="text-[16px] leading-snug font-medium">{node.label}</span>
          <span className="type-meta text-secondary">{node.note}</span>
        </div>
        <span
          className={`type-meta text-center text-secondary [text-wrap:balance] ${
            node.optional ? "border-t-2 border-dotted border-muted pt-2" : ""
          }`}
        >
          {node.site}
        </span>
      </div>
      {edge ? <Edge label={edge} /> : null}
    </>
  );
}

/** A solid execution arrow with its noun. Vertical below 900px, horizontal above. */
function Edge({ label }: { label: string }) {
  return (
    <div aria-hidden="true" className="flex items-center gap-2 pl-4 md:h-16 md:w-10 md:flex-none md:flex-col md:justify-center md:gap-0 md:pl-0">
      <svg viewBox="0 0 40 16" className="h-4 w-10 rotate-90 text-primary md:rotate-0" focusable="false">
        <path d="M1 8h32" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M29 3.5 34.5 8 29 12.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="type-small whitespace-nowrap text-muted md:sr-only">{label}</span>
    </div>
  );
}

/** The one dark surface: the conceptual categories a release carries, no values. */
function ReleaseRecordPanel() {
  const { panelLabel, panelStatus, panelFooter, groups } = EXECUTION.boundaryDisclosure;
  return (
    <div className="mt-5 rounded-lg bg-inverse text-inverse-text">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-1 border-b border-inverse-muted/30 px-4 py-3 font-mono text-[13px] uppercase tracking-[0.06em] text-inverse-muted">
        <span>{panelLabel}</span>
        <span>{panelStatus}</span>
      </div>
      <div className="px-4 py-4">
        <dl className="type-code grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-[136px_minmax(0,1fr)]">
          {groups.flatMap((group) => [
            <dt key={group.title} className="mt-3 font-mono text-[12px] uppercase tracking-[0.06em] text-inverse-muted first:mt-0 sm:col-span-2">
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
      <div className="border-t border-inverse-muted/30 px-4 py-3 font-mono text-[13px] tracking-[0.02em] text-inverse-muted">{panelFooter}</div>
    </div>
  );
}
