"use client";

import { HERO } from "@/content/homepage";
import { useTrace } from "@/lib/use-trace";
import { ReleaseDetails, ReleaseEnvelope } from "./ReleaseEnvelope";

/**
 * The compact release figure with its one quiet sequence: the trained model
 * lights, the release connector travels, the six release parts take their
 * accent in turn, the connector to the controller travels, the controller
 * lights, and the boundary takes a final emphasis before everything lets
 * go. About 2.6 seconds, once, when the figure enters view; a small Replay
 * control repeats it. The static figure is complete at first paint and
 * nothing moves in layout. Lifecycle is useTrace; under reduced motion the
 * control toggles a static highlight of the release instead.
 */
const HERO_MS = 2700;

export function HeroFigure() {
  // Plays only once the figure is well into view (its top above the upper 60% of the viewport).
  const { phase, play, reduced, rootRef, startRef } = useTrace(HERO_MS, { entryMargin: "-40%" });
  const copy = HERO.replay;
  const label = reduced
    ? phase === "emphasis"
      ? copy.clearHighlight
      : copy.highlight
    : phase === "playing"
      ? copy.playing
      : copy.replay;
  const status = phase === "playing" ? copy.statusPlaying : phase === "done" ? copy.statusDone : phase === "emphasis" ? copy.statusHighlighted : "";

  return (
    <div ref={rootRef} data-trace-hero={phase} className="hero-trace">
      <div ref={startRef} aria-hidden="true" data-hero-start className="h-px w-px" />
      <Endpoint kind="model" />
      <ReleaseEnvelope />
      <Endpoint kind="robot" />
      <figcaption id="hero-diagram-caption" className="type-caption mt-4 text-secondary">
        {HERO.caption}
      </figcaption>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2" data-hero-controls>
        <button
          type="button"
          className="btn btn-secondary min-h-12 px-4 text-[0.9375rem]"
          onClick={play}
          aria-describedby="hero-diagram-caption"
          aria-pressed={reduced ? phase === "emphasis" : undefined}
          data-hero-replay
        >
          <span aria-hidden="true" className="trace-dot" />
          {label}
        </button>
        <span role="status" aria-live="polite" className="type-meta min-h-6 w-full text-accent-hover sm:w-auto" data-hero-status>
          {status}
        </span>
      </div>
      <ReleaseDetails className="mt-3" />
    </div>
  );
}

/** The model above the envelope and the controller below it, each with its ownership and edge noun. */
function Endpoint({ kind }: { kind: "model" | "robot" }) {
  const copy = HERO.endpoints[kind];
  const release = kind === "model";
  const arrow = (
    <span className={`hero-edge flex items-center gap-2 py-1 pl-4 type-small text-secondary ${release ? "hero-edge-model" : "hero-edge-robot"}`}>
      <svg viewBox="0 0 16 32" className={`h-7 w-4 overflow-visible ${release ? "text-accent" : "text-primary"}`} aria-hidden="true" focusable="false">
        <path d="M8 1v24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeDasharray={release ? "5 4" : undefined} />
        <path d="M3.5 21 8 26.5 12.5 21" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        <path className="trace-overlay" d="M8 1v25" pathLength={1} fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      {copy.edge}
    </span>
  );
  const node = (
    <div
      data-endpoint={kind}
      className="hero-node flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded border border-dashed border-strong px-4 py-2.5"
    >
      <span className="text-base leading-snug font-medium text-primary">{copy.title}</span>
      <span className="type-meta text-secondary">{copy.ownership}</span>
    </div>
  );
  return kind === "model" ? (
    <div className="mb-1">
      {node}
      {arrow}
    </div>
  ) : (
    <div className="mt-1">
      {arrow}
      {node}
    </div>
  );
}
