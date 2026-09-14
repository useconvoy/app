import { PARTNERSHIP } from "@/content/homepage";
import { ArrowGlyph } from "./Hero";
import { Section, SectionHeading } from "./Section";

/** Open warm-paper band, with the deployment ingredients on the right. */
export function DesignPartnerSection() {
  return (
    <Section id={PARTNERSHIP.id} labelledBy="partnership-heading">
      <div className="partnership-layout">
        <div>
          <SectionHeading id="partnership-heading" eyebrow={PARTNERSHIP.eyebrow} heading={PARTNERSHIP.heading} lead={PARTNERSHIP.lead} />
          <a href={PARTNERSHIP.cta.href} className="btn btn-primary mt-7">
            {PARTNERSHIP.cta.label}
            <ArrowGlyph />
          </a>
        </div>
        <div className="partnership-fit">
          <h3 className="type-eyebrow">{PARTNERSHIP.fitTitle}</h3>
          <ul>
            {PARTNERSHIP.fit.map((item, index) => (
              <li key={item.label}>
                <span className="fit-number" aria-hidden="true">0{index + 1}</span>
                <div>
                  <p className="type-h4 text-primary">{item.label}</p>
                  <p className="type-body mt-2 text-secondary">{item.note}</p>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </Section>
  );
}
