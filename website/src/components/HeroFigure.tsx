import { HERO } from "@/content/homepage";
import { EtchedField } from "./EtchedField";
import { ReleaseDetails, ReleaseEnvelope } from "./ReleaseEnvelope";

/**
 * The compact release figure, static and complete at first paint: the
 * trained model above, the release envelope with its six parts, the robot
 * controller below, each endpoint with its ownership and edge noun, then
 * the caption and the details disclosure. The diagram itself never moves;
 * it sits on the etched field (EtchedField), a decorative grid with quiet
 * rings behind it.
 */
export function HeroFigure() {
  return (
    <div data-hero-figure>
      <EtchedField className="px-4 py-5 sm:px-7 sm:py-7">
        <Endpoint kind="model" />
        <ReleaseEnvelope />
        <Endpoint kind="robot" />
      </EtchedField>
      <figcaption id="hero-diagram-caption" className="type-caption mt-4 text-secondary">
        {HERO.caption}
      </figcaption>
      <ReleaseDetails className="mt-3" />
    </div>
  );
}

/** The model above the envelope and the controller below it, each with its ownership and edge noun. */
function Endpoint({ kind }: { kind: "model" | "robot" }) {
  const copy = HERO.endpoints[kind];
  const release = kind === "model";
  const arrow = (
    <span className="flex items-center gap-2 py-1 pl-4 type-small text-secondary">
      <svg viewBox="0 0 16 32" className={`h-7 w-4 overflow-visible ${release ? "text-accent" : "text-primary"}`} aria-hidden="true" focusable="false">
        <path d="M8 1v24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeDasharray={release ? "5 4" : undefined} />
        <path d="M3.5 21 8 26.5 12.5 21" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {copy.edge}
    </span>
  );
  const node = (
    <div data-endpoint={kind} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded border border-dashed border-strong bg-background px-4 py-2.5">
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
