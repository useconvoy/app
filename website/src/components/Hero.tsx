import { HERO, SITE } from "@/content/homepage";
import { ReleaseEnvelope } from "./ReleaseEnvelope";

/**
 * Category, headline, explanation, two actions and the development-stage
 * line, beside the release envelope. The three-object path from the brief
 * reads top to bottom on the figure side: the trained model enters the
 * release, and the release is deployed to the robot controller. Desktop is
 * a 5/7 split; below 900px the copy comes first. Nothing is animated.
 */
export function Hero() {
  return (
    <section id="top" aria-labelledby="hero-heading" className="bg-background">
      <div className="mx-auto grid w-full max-w-(--content-max) gap-12 px-5 pt-12 pb-14 sm:px-8 sm:pt-16 sm:pb-[72px] md:grid-cols-12 md:gap-8 lg:px-16 lg:pt-20 lg:pb-[112px]">
        <div className="md:col-span-5">
          <p className="type-eyebrow">{HERO.eyebrow}</p>
          <h1 id="hero-heading" className="type-display mt-4 text-primary">
            {HERO.headline}
          </h1>
          <p className="type-lead mt-6 text-secondary">{HERO.lede}</p>
          <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
            <a href={HERO.primary.href} className="btn btn-primary">
              {HERO.primary.label}
              <ArrowGlyph />
            </a>
            <a href={HERO.secondary.href} className="btn btn-secondary">
              {HERO.secondary.label}
            </a>
          </div>
          <div className="mt-6 border-t border-subtle pt-4">
            <span className="inline-flex items-center gap-2 rounded-sm bg-information-tint px-3 py-1 font-mono text-[13px] font-medium text-information">
              <span aria-hidden="true" className="h-2 w-2 rounded-full bg-current" />
              Stage: {SITE.stage}
            </span>
            <p className="type-small mt-2 text-secondary">{HERO.stage.replace(/^In development\.\s*/, "")}</p>
          </div>
        </div>

        <figure className="md:col-span-7" aria-labelledby="hero-diagram-title">
          <figcaption className="sr-only" id="hero-diagram-title">
            {HERO.diagramLabel}: a trained model enters a Convoy release, which is deployed to the robot controller.
          </figcaption>
          <Endpoint kind="model" />
          <ReleaseEnvelope />
          <Endpoint kind="robot" />
          <p className="type-small mt-4 text-muted">
            <span className="font-medium text-secondary">{HERO.diagramLabel}.</span> {HERO.caption}
          </p>
        </figure>
      </div>
    </section>
  );
}

/** The model above the envelope and the controller below it, each with its edge noun. */
function Endpoint({ kind }: { kind: "model" | "robot" }) {
  const copy = HERO.endpoints[kind];
  const arrow = (
    <span className="flex items-center gap-2 py-2 pl-4 type-small text-muted">
      <svg viewBox="0 0 16 40" className="h-8 w-4 text-primary" aria-hidden="true" focusable="false">
        <path d="M8 1v32M3.5 29 8 34.5 12.5 29" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      {copy.edge}
    </span>
  );
  const node = (
    <div className={`flex flex-wrap items-baseline gap-x-3 gap-y-1 rounded border px-4 py-3 ${kind === "robot" ? "border-dashed border-strong bg-transparent" : "border-strong bg-surface-subtle"}`}>
      <span className="type-label text-primary">{copy.title}</span>
      <span className="font-mono text-[11px] tracking-[0.02em] text-secondary">{copy.note}</span>
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
    <svg viewBox="0 0 16 16" className="h-4 w-4 flex-none" aria-hidden="true" focusable="false">
      <path d="M2 8h11m0 0L9 4m4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
