import { PARTNERSHIP } from "@/content/homepage";
import { ArrowGlyph } from "./Hero";
import { Section, SectionHeading } from "./Section";

/** One bordered white block: heading, lead and the action left; the fit list right. */
export function DesignPartnerSection() {
  return (
    <Section id={PARTNERSHIP.id} labelledBy="partnership-heading">
      <div className="grid gap-8 rounded-lg border border-subtle bg-surface p-6 sm:p-8 md:grid-cols-2 md:gap-12 lg:p-12">
        <div>
          <SectionHeading id="partnership-heading" eyebrow={PARTNERSHIP.eyebrow} heading={PARTNERSHIP.heading} lead={PARTNERSHIP.lead} />
          <a href={PARTNERSHIP.cta.href} className="btn btn-primary mt-7">
            {PARTNERSHIP.cta.label}
            <ArrowGlyph />
          </a>
        </div>
        <div className="md:pt-9">
          <h3 className="type-eyebrow">{PARTNERSHIP.fitTitle}</h3>
          <ul className="mt-2 divide-y divide-subtle border-y border-subtle">
            {PARTNERSHIP.fit.map((item) => (
              <li key={item.label} className="py-3">
                <p className="type-body font-medium text-primary">{item.label}</p>
                <p className="type-small mt-0.5 text-secondary">{item.note}</p>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  );
}
