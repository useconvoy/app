"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { EXECUTION } from "@/content/homepage";

/**
 * The execution-path figure with its one motion: a trace that travels the
 * connectors from Sensors to Robot controller once, with a brief border
 * emphasis on each node as it is reached. The complete static diagram
 * (labels, connectors, boundary) is rendered at first paint and the trace
 * is an overlay drawn by CSS keyframes; no requestAnimationFrame loop, no
 * idle timer.
 *
 * It plays once when the start of the path comes into view (a marker just
 * before the first node, so a figure taller than a short viewport still
 * qualifies), unless the person prefers reduced motion or has asked to
 * save data. The single autoplay is consumed the moment it starts, so an
 * interrupted run never restarts on re-entry. It can be replayed with the
 * Trace the path button (click, tap, Enter, Space); pointer movement never
 * starts it. A run is cancelled, back to the complete static diagram, when
 * the figure leaves the viewport entirely, the document is hidden, the
 * reduced-motion preference changes, the viewport is resized or rotated,
 * or the component unmounts. Under reduced motion the button briefly
 * emphasises the path instead of animating it.
 *
 * Nothing here suggests live robot execution: the trace is the reading
 * order of the diagram, and the caption says what placement depends on.
 */
type Phase = "idle" | "playing" | "done" | "emphasis";

const TRACE_MS = 2000;
const EMPHASIS_MS = 1200;

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
  const [phase, setPhase] = useState<Phase>("idle");
  const rootRef = useRef<HTMLDivElement>(null);
  const startRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  const frame = useRef<number | null>(null);
  const autoplayed = useRef(false);
  /* Each activation gets a run id; a frame or timeout from an earlier run
   * finds the id has moved on and does nothing. */
  const run = useRef(0);

  const clearPending = useCallback(() => {
    if (frame.current !== null) {
      window.cancelAnimationFrame(frame.current);
      frame.current = null;
    }
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
  }, []);

  const reducedMotion = () =>
    typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const saveData = () => {
    const nav = typeof navigator === "undefined" ? undefined : (navigator as Navigator & { connection?: { saveData?: boolean } });
    return Boolean(nav?.connection?.saveData);
  };

  const cancel = useCallback(() => {
    run.current += 1;
    clearPending();
    setPhase((current) => (current === "playing" || current === "emphasis" ? "idle" : current));
  }, [clearPending]);

  const play = useCallback(() => {
    // Any activation, including the button, uses up the one autoplay so a
    // later entry of the start marker never replays unasked.
    autoplayed.current = true;
    const id = (run.current += 1);
    clearPending();
    if (document.visibilityState === "hidden") return;
    if (reducedMotion()) {
      setPhase("emphasis");
      timer.current = window.setTimeout(() => {
        timer.current = null;
        if (run.current !== id) return;
        setPhase("done");
      }, EMPHASIS_MS);
      return;
    }
    // Restarting a running trace: drop the animation for one frame so the
    // keyframes begin again from the start. The frame is tracked so a
    // cancellation before it fires wins, and it re-checks the conditions.
    setPhase("idle");
    frame.current = window.requestAnimationFrame(() => {
      frame.current = null;
      if (run.current !== id) return;
      if (document.visibilityState === "hidden" || reducedMotion()) return;
      setPhase("playing");
      timer.current = window.setTimeout(() => {
        timer.current = null;
        if (run.current !== id) return;
        setPhase("done");
      }, TRACE_MS);
    });
  }, [clearPending]);

  // Autoplay once when the start of the path is inside the viewport with a
  // little room below it. The figure as a whole only has to be partly in
  // view, so a tall figure in a short or landscape viewport still qualifies.
  useEffect(() => {
    const start = startRef.current;
    if (!start || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting || autoplayed.current) return;
        if (reducedMotion() || saveData()) return;
        autoplayed.current = true;
        play();
      },
      { rootMargin: "0px 0px -15% 0px", threshold: 0 },
    );
    observer.observe(start);
    return () => observer.disconnect();
  }, [play]);

  // A run is cancelled only when the figure is entirely out of view.
  useEffect(() => {
    const root = rootRef.current;
    if (!root || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) cancel();
      },
      { threshold: 0 },
    );
    observer.observe(root);
    return () => observer.disconnect();
  }, [cancel]);

  // A change of motion preference cancels at once; a resize or rotation
  // during a run settles back to the static diagram.
  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => cancel();
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [cancel]);

  useEffect(() => {
    if (phase !== "playing") return;
    const onResize = () => cancel();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [phase, cancel]);

  // A hidden document cancels a run; unmount clears the timer.
  useEffect(() => {
    function onVisibility() {
      if (document.visibilityState === "hidden") cancel();
    }
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      run.current += 1;
      clearPending();
    };
  }, [cancel, clearPending]);

  const groups = runs();
  let edgeIndex = 0;
  let nodeIndex = 0;

  return (
    <div ref={rootRef} data-trace={phase} className="trace @container">
      <div ref={startRef} aria-hidden="true" data-trace-start className="h-px w-px" />
      <div className="flex flex-col gap-1 @4xl:flex-row @4xl:items-stretch @4xl:gap-0" data-execution-path>
        {groups.map((group, groupIndex) => {
          const convoy = group.owner === "convoy";
          const body = group.nodes.map((node, i) => {
            const order = nodeIndex++;
            const edge = i < group.nodes.length - 1 ? { label: EXECUTION.edges[edgeIndex], order: edgeIndex++ } : undefined;
            return <NodeAndEdge key={node.key} node={node} order={order} edge={edge} />;
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
        <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
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
          <button
            type="button"
            className="btn btn-secondary min-h-12 px-5 text-[0.9375rem]"
            onClick={play}
            aria-describedby="execution-diagram-caption"
            data-trace-button
          >
            {EXECUTION.traceLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

function NodeAndEdge({ node, order, edge }: { node: Node; order: number; edge?: { label: string; order: number } }) {
  const external = node.owner === "robot";
  const model = "model" in node && node.model;
  return (
    <>
      <div className="flex min-w-0 flex-1 flex-col gap-1.5" data-node={node.key}>
        <div
          style={{ ["--i" as string]: order }}
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
 * A solid execution arrow with its noun. Vertical below 900px, horizontal
 * above. The base line is always drawn; the trace overlay on top is
 * invisible until a run draws it in, then fades out again.
 */
function Edge({ label, order }: { label: string; order: number }) {
  return (
    <div
      aria-hidden="true"
      style={{ ["--i" as string]: order }}
      className="trace-edge flex items-center gap-2 py-0.5 pl-4 @4xl:h-16 @4xl:w-10 @4xl:flex-none @4xl:flex-col @4xl:justify-center @4xl:gap-0 @4xl:py-0 @4xl:pl-0"
    >
      <svg viewBox="0 0 40 16" className="h-4 w-10 rotate-90 text-primary @4xl:rotate-0" focusable="false">
        <path d="M1 8h32" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <path d="M29 3.5 34.5 8 29 12.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <path className="trace-overlay" d="M1 8h32" pathLength={1} stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      </svg>
      <span className="type-small whitespace-nowrap text-muted @4xl:sr-only">{label}</span>
    </div>
  );
}
