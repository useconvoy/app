import { HERO, PROBLEM } from "@/content/homepage";
import { HeroFigure } from "./HeroFigure";
import { Section, SectionHeading } from "./Section";

/** One heading, one lead, four concise dependency rows. */
export function Problem() {
  return (
    <Section id={PROBLEM.id} labelledBy="problem-heading">
      <div className="problem-layout">
        <div className="problem-copy">
          <SectionHeading id="problem-heading" eyebrow={PROBLEM.eyebrow} heading={PROBLEM.heading} lead={PROBLEM.lead} />
          <ol className="dependency-list" aria-label={PROBLEM.columnLabel}>
            {PROBLEM.dependencies.map((item, index) => (
              <li key={item.label}>
                <h3 className="flex items-baseline gap-3 type-body font-medium text-primary">
                  <span className="dependency-number" aria-hidden="true">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  {item.label}
                </h3>
                <p className="type-body text-secondary">{item.body}</p>
              </li>
            ))}
          </ol>
        </div>
        <figure className="release-figure" aria-labelledby="hero-diagram-title" aria-describedby="hero-diagram-description hero-diagram-caption">
          <p id="hero-diagram-title" className="drawing-title type-eyebrow">{HERO.diagramHeading}<span aria-hidden="true">↘</span></p>
          <p id="hero-diagram-description" className="sr-only">{HERO.description}</p>
          <HeroFigure />
        </figure>
      </div>
    </Section>
  );
}
