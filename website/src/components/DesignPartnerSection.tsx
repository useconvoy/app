import { PARTNERSHIP } from "@/content/homepage";
import { ArrowGlyph } from "./Hero";
import { Section, SectionHeading } from "./Section";

/** One bordered white block: scope on the left, the fit checklist and the action on the right. */
export function DesignPartnerSection() {
  return (
    <Section id={PARTNERSHIP.id} labelledBy="partnership-heading">
      <div className="grid gap-10 rounded-lg border border-subtle bg-surface p-6 sm:p-10 md:grid-cols-2 md:gap-12 lg:p-14">
        <div>
          <SectionHeading id="partnership-heading" eyebrow={PARTNERSHIP.eyebrow} heading={PARTNERSHIP.heading} lead={PARTNERSHIP.lead} />
          <div className="prose-measure mt-6 space-y-4 text-secondary type-body">
            {PARTNERSHIP.body.map((paragraph) => (
              <p key={paragraph}>{paragraph}</p>
            ))}
          </div>
          <a href={PARTNERSHIP.cta.href} className="btn btn-primary mt-8">
            {PARTNERSHIP.cta.label}
            <ArrowGlyph />
          </a>
        </div>
        <div className="md:pt-10">
          <h3 className="type-eyebrow">{PARTNERSHIP.fitTitle}</h3>
          <ul className="mt-2 divide-y divide-subtle border-y border-subtle">
            {PARTNERSHIP.fit.map((item) => (
              <li key={item.label} className="py-4">
                <p className="type-body font-medium text-primary">{item.label}</p>
                <p className="type-small mt-1 text-secondary">{item.note}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  );
}
