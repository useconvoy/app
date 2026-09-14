import { EXECUTION } from "@/content/homepage";

/**
 * The execution-path figure, static and complete at first paint: five
 * nodes from Sensors to Robot controller joined by solid execution arrows,
 * the three Convoy-scoped nodes inside the labelled dashed runtime boundary,
 * a placement label under each node, the feedback line, the caption and the
 * legend. Vertical below a 56rem container, a row above it. Nothing moves
 * and nothing here is interactive; the caption says what placement depends
 * on and the section's visually hidden sentence states the relationships.
 */
type Node = (typeof EXECUTION.nodes)[number];

function runs(): { owner: Node["owner"]; nodes: Node[] }[] {
  const out: { owner: Node["owner"]; nodes: Node[] }[] = [];
  for (const node of EXECUTION.nodes) {
    const last = out[out.length - 1];
    if (last && last.owner === node.owner) last.nodes.push(node);
    else out.push({ owner: node.owner, nodes: [node] });
  }
  return out;
}

export function ExecutionDiagram() {
  const groups = runs();
  let edgeIndex = 0;

  return (
    <div className="@container flex flex-col" data-execution-diagram>
      <div className="flex flex-col gap-1 @4xl:flex-row @4xl:items-stretch @4xl:gap-0" data-execution-path>
        {groups.map((group, groupIndex) => {
          const convoy = group.owner === "convoy";
          const body = group.nodes.map((node, i) => {
            const edge = i < group.nodes.length - 1 ? EXECUTION.edges[edgeIndex++] : undefined;
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
                  className="relative my-2 flex flex-col gap-1 rounded-lg border-2 border-dashed border-accent p-3 @4xl:my-0 @4xl:mx-1 @4xl:flex-[3_1_auto] @4xl:flex-row @4xl:items-stretch @4xl:gap-0 @4xl:pt-7"
                >
                  <span className="mb-3 font-mono text-sm font-medium tracking-[0.02em] text-accent @4xl:absolute @4xl:-top-[11px] @4xl:left-3 @4xl:mb-0 @4xl:bg-background @4xl:px-2">
                    {EXECUTION.scope}
                  </span>
                  {body}
                </div>
              ) : (
                <div className="flex flex-col gap-1 @4xl:flex-[1_1_auto] @4xl:flex-row @4xl:items-stretch @4xl:gap-0 @4xl:pt-7">{body}</div>
              )}
              {between}
            </div>
          );
        })}
      </div>

      {/* Feedback: new observations and robot state travel back to the sensors. */}
      <div className="execution-feedback mt-3 flex items-center gap-3 type-small text-secondary">
        <svg viewBox="0 0 120 20" className="h-5 w-[72px] flex-none text-strong @4xl:w-[120px]" aria-hidden="true" focusable="false">
          <path d="M115 4v8H8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeDasharray="4 4" />
          <path d="M13 8l-6 4 6 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span>{EXECUTION.feedback}</span>
      </div>

      <div className="mt-4 flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <span id="execution-diagram-caption" className="type-caption max-w-[66ch] text-secondary">
          {EXECUTION.caption}
        </span>
        <ul className="flex flex-wrap gap-x-5 gap-y-1 text-sm text-secondary" aria-label="Diagram legend">
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
      <div className="flex min-w-0 flex-1 flex-col gap-1.5" data-node={node.key}>
        <div
          className={`flex min-h-14 flex-1 flex-col items-center justify-center gap-[2px] rounded border px-4 py-2.5 text-center @4xl:min-h-16 ${
            external
              ? "border-dashed border-strong bg-transparent text-primary"
              : model
                ? "border-primary bg-surface text-primary"
                : "border-strong bg-surface-subtle text-primary"
          }`}
        >
          <span className="text-base leading-snug font-medium">{node.label}</span>
          <span className="type-meta text-secondary">{node.note}</span>
        </div>
        <span
          className={`type-meta text-center text-secondary [text-wrap:balance] ${
            node.optional ? "border-t-2 border-dotted border-muted pt-1.5" : ""
          }`}
        >
          {node.site}
        </span>
      </div>
      {edge ? <Edge label={edge} /> : null}
    </>
  );
}

/** A solid execution arrow with its noun. Vertical below the container breakpoint, horizontal above. */
function Edge({ label }: { label: string }) {
  return (
    <div
      aria-hidden="true"
      className="flex min-h-11 items-center gap-2 py-0.5 pl-4 @4xl:h-16 @4xl:w-10 @4xl:flex-none @4xl:flex-col @4xl:justify-center @4xl:gap-0 @4xl:py-0 @4xl:pl-0"
      data-edge
    >
      <svg viewBox="0 0 40 16" className="h-4 w-10 rotate-90 overflow-visible text-primary @4xl:rotate-0" focusable="false">
        <path d="M1 8h32" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M29 3.5 34.5 8 29 12.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="type-small whitespace-nowrap text-muted @4xl:sr-only">{label}</span>
    </div>
  );
}
