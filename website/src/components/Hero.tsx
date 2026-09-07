import { HERO } from "@/content/homepage";
import { HeroFigure } from "./HeroFigure";

/**
 * Category, headline, lead and two actions in one column; the compact
 * release figure (HeroFigure, static) in the other.
 * Balanced columns from 900px; below that the copy and the primary action
 * come first and the figure follows.
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
          <HeroFigure />
        </figure>
      </div>
    </section>
  );
}

export function ArrowGlyph() {
  return (
    <svg viewBox="0 0 16 16" className="btn-arrow h-4 w-4 flex-none" aria-hidden="true" focusable="false">
      <path d="M2 8h11m0 0L9 4m4 4-4 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
