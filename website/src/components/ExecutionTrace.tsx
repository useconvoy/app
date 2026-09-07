"use client";

import { useState, type AnimationEvent } from "react";

import { EXECUTION } from "@/content/homepage";
import { useTrace } from "@/lib/use-trace";

/**
 * The execution-path figure with its one motion: a terracotta trace that
 * travels the connectors from Sensors to Robot controller, filling each
 * node as it is reached, over about 2.5 seconds, then releases. The complete
 * static diagram (labels, connectors, boundary) is rendered at first paint;
 * the trace is CSS keyframes on an overlay stroke and the node surfaces.
 *
 * The control gives immediate feedback: its label changes while a run is
 * in progress and a status line names the node the trace has reached. On a
 * narrow container the control row sits at the top of the figure, so a tap
 * plays the nodes directly beneath it; on a wide one it sits with the
 * caption. Lifecycle (autoplay once from the start marker, cancellation,
 * reduced motion as a static highlight toggle) is useTrace.
 *
 * Nothing here suggests live robot execution: the trace is the reading
 * order of a conceptual diagram, and the caption says what placement
 * depends on.
 */
const TRACE_MS = 2500;

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

export function ExecutionTrace() {
  const { phase, play, reduced, rootRef, startRef } = useTrace(TRACE_MS);
  // The node the trace has reached, from the node's own animation start; only shown while playing.
  const [reached, setReached] = useState<string | null>(null);
  const shown = phase === "playing" ? reached : null;
  const start = () => {
    setReached(null);
    play();
  };
  const onNodeAnimationStart = (label: string) => (event: AnimationEvent<HTMLDivElement>) => {
    if (event.animationName.startsWith("trace-node")) setReached(label);
  };

  const copy = EXECUTION.trace;
  const label = reduced
    ? phase === "emphasis"
      ? copy.clearHighlight
      : copy.highlight
    : phase === "playing"
      ? copy.playing
      : phase === "done"
        ? copy.again
        : copy.start;
  const status =
    phase === "playing"
      ? `${copy.statusPlaying}${shown ? ` · ${shown}` : ""}`
      : phase === "done"
        ? copy.statusDone
        : phase === "emphasis"
          ? copy.statusHighlighted
          : "";

  const groups = runs();
  let edgeIndex = 0;
  let nodeIndex = 0;

  return (
    <div ref={rootRef} data-trace={phase} className="trace @container flex flex-col">
      <div ref={startRef} aria-hidden="true" data-trace-start className="h-px w-px" />

      {/* The control and its progress line: first on a narrow container, with the caption on a wide one. */}
      <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-2 @4xl:order-last @4xl:mt-3 @4xl:mb-0" data-trace-controls>
        <button
          type="button"
          className="btn btn-secondary min-h-12 px-5 text-[0.9375rem]"
          onClick={start}
          aria-describedby="execution-diagram-caption"
          aria-pressed={reduced ? phase === "emphasis" : undefined}
          data-trace-button
        >
          <span aria-hidden="true" className="trace-dot" />
          {label}
        </button>
        <span role="status" aria-live="polite" className="type-meta min-h-6 w-full text-accent-hover @4xl:w-auto" data-trace-status>
          {status}
        </span>
      </div>

      <div className="flex flex-col gap-1 @4xl:flex-row @4xl:items-stretch @4xl:gap-0" data-execution-path>
        {groups.map((group, groupIndex) => {
          const convoy = group.owner === "convoy";
          const body = group.nodes.map((node, i) => {
            const order = nodeIndex++;
            const edge = i < group.nodes.length - 1 ? { label: EXECUTION.edges[edgeIndex], order: edgeIndex++ } : undefined;
            return <NodeAndEdge key={node.key} node={node} order={order} edge={edge} onStart={onNodeAnimationStart(node.label)} />;
          });
          const between =
            groupIndex < groups.length - 1 ? <Edge key={`edge-${groupIndex}`} label={EXECUTION.edges[edgeIndex]} order={edgeIndex++} /> : null;
          return (
            <div key={group.owner + groupIndex} className="contents">
              {convoy ? (
                <div
                  role="group"
                  aria-label={EXECUTION.scope}
                  data-runtime-boundary
                  className="relative my-2 flex flex-col gap-1 rounded-lg border-2 border-dashed border-accent px-3 pt-7 pb-3 @4xl:my-0 @4xl:mx-1 @4xl:flex-[3_1_auto] @4xl:flex-row @4xl:items-stretch @4xl:gap-0"
                >
                  <span className="absolute -top-[11px] left-3 bg-background px-2 font-mono text-sm font-medium tracking-[0.02em] text-accent">
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
      <div className="mt-3 flex items-center gap-3 type-small text-secondary">
        <svg viewBox="0 0 120 20" className="h-5 w-[120px] flex-none text-strong" aria-hidden="true" focusable="false">
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

function NodeAndEdge({
  node,
  order,
  edge,
  onStart,
}: {
  node: Node;
  order: number;
  edge?: { label: string; order: number };
  onStart: (event: AnimationEvent<HTMLDivElement>) => void;
}) {
  const external = node.owner === "robot";
  const model = "model" in node && node.model;
  return (
    <>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5" data-node={node.key}>
        <div
          style={{ ["--i" as string]: order }}
          onAnimationStart={onStart}
          className={`trace-node flex min-h-14 flex-1 flex-col items-center justify-center gap-[2px] rounded border px-4 py-2.5 text-center @4xl:min-h-16 ${
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
      {edge ? <Edge label={edge.label} order={edge.order} /> : null}
    </>
  );
}

/**
 * A solid execution arrow with its noun. Vertical below the container
 * breakpoint, horizontal above. The base line is always drawn; the trace
 * overlay on top is invisible until a run draws it in terracotta.
 */
function Edge({ label, order }: { label: string; order: number }) {
  return (
    <div
      aria-hidden="true"
      style={{ ["--i" as string]: order }}
      className="trace-edge flex items-center gap-2 py-0.5 pl-4 @4xl:h-16 @4xl:w-10 @4xl:flex-none @4xl:flex-col @4xl:justify-center @4xl:gap-0 @4xl:py-0 @4xl:pl-0"
    >
      <svg viewBox="0 0 40 16" className="h-4 w-10 rotate-90 overflow-visible text-primary @4xl:rotate-0" focusable="false">
        <path d="M1 8h32" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M29 3.5 34.5 8 29 12.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <path className="trace-overlay" d="M1 8h33" pathLength={1} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <span className="type-small whitespace-nowrap text-muted @4xl:sr-only">{label}</span>
    </div>
  );
}
