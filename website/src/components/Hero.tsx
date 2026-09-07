import { HERO } from "@/content/homepage";
import { ReleaseDetails, ReleaseEnvelope } from "./ReleaseEnvelope";

/**
 * Category, headline, lead and two actions in one column; the compact
 * release figure in the other: the trained model supplied by your team,
 * the dashed release boundary Convoy is building, and the robot controller
 * and safety system outside it. Balanced columns from 900px; below that the
 * copy and the primary action come first and the figure follows. The
 * example contents of each release part sit behind one disclosure.
 */
export function Hero() {
  return (
    <section id="top" aria-labelledby="hero-heading" className="bg-background">
      <div className="mx-auto grid w-full max-w-(--content-max) gap-8 px-5 pt-8 pb-10 sm:px-8 sm:pt-14 sm:pb-16 md:grid-cols-2 md:gap-10 md:px-10 lg:gap-14 lg:px-16 lg:pt-16 lg:pb-20">
        <div>
          <p className="type-eyebrow">{HERO.eyebrow}</p>
          <h1 id="hero-heading" className="type-display mt-4 text-primary">
            {HERO.headline}
          </h1>
          <p className="type-lead mt-5 text-secondary">{HERO.lede}</p>
          <div className="mt-7 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
            <a href={HERO.primary.href} className="btn btn-primary">
              {HERO.primary.label}
              <ArrowGlyph />
            </a>
            <a href={HERO.secondary.href} className="btn btn-secondary">
              {HERO.secondary.label}
            </a>
          </div>
        </div>

        <figure className="md:pt-1" aria-labelledby="hero-diagram-title" aria-describedby="hero-diagram-description hero-diagram-caption">
          <p id="hero-diagram-title" className="type-eyebrow mb-4">
            {HERO.diagramHeading}
          </p>
          <p id="hero-diagram-description" className="sr-only">
            {HERO.description}
          </p>
          <Endpoint kind="model" />
          <ReleaseEnvelope />
          <Endpoint kind="robot" />
          <figcaption id="hero-diagram-caption" className="type-caption mt-4 text-secondary">
            {HERO.caption}
          </figcaption>
          <ReleaseDetails className="mt-4" />
        </figure>
      </div>
    </section>
  );
}

/** The model above the envelope and the controller below it, each with its ownership and edge noun. */
function Endpoint({ kind }: { kind: "model" | "robot" }) {
  const copy = HERO.endpoints[kind];
  const release = kind === "model";
  const arrow = (
    <span className="flex items-center gap-2 py-1 pl-4 type-small text-secondary">
      <svg viewBox="0 0 16 32" className={`h-7 w-4 ${release ? "text-accent" : "text-primary"}`} aria-hidden="true" focusable="false">
        <path d="M8 1v24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeDasharray={release ? "5 4" : undefined} />
        <path d="M3.5 21 8 26.5 12.5 21" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {copy.edge}
    </span>
  );
  const node = (
    <div data-endpoint={kind} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded border border-dashed border-strong px-4 py-2.5">
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

export function ArrowGlyph() {
  return (
    <svg viewBox="0 0 16 16" className="btn-arrow h-4 w-4 flex-none" aria-hidden="true" focusable="false">
      <path d="M2 8h11m0 0L9 4m4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
