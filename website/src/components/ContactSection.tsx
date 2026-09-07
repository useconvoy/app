import { CONTACT, CONTACT_MAILTO } from "@/content/homepage";
import { ArrowGlyph } from "./Hero";
import { Section, SectionHeading } from "./Section";

/**
 * The contact block. Inquiries go by email: the primary action is a mailto
 * link that opens the visitor's own mail app with the subject and a short
 * template filled in, the address is shown as selectable text beside it,
 * and three prompts say what a useful first note covers. Nothing is
 * collected or stored on the site. The address is one constant in
 * src/content/homepage.ts, overridable with NEXT_PUBLIC_CONTACT_EMAIL.
 */
export function ContactSection() {
  return (
    <Section id={CONTACT.id} labelledBy="contact-heading">
      <div className="grid gap-10 md:grid-cols-12 md:gap-8">
        <div className="md:col-span-5">
          <SectionHeading id="contact-heading" eyebrow={CONTACT.eyebrow} heading={CONTACT.heading} lead={CONTACT.lead} />
        </div>
        <div className="md:col-span-7">
          <div className="rounded-lg border border-subtle bg-surface p-6 sm:p-8 lg:p-10">
            <h3 className="type-h3 text-primary">{CONTACT.blockTitle}</h3>
            <p className="type-body mt-3 text-secondary">{CONTACT.guidanceIntro}</p>
            <ul className="mt-4 divide-y divide-subtle border-y border-subtle">
              {CONTACT.guidance.map((item) => (
                <li key={item.label} className="flex flex-col gap-1 py-3 sm:flex-row sm:gap-6">
                  <span className="type-label w-44 flex-none text-primary">{item.label}</span>
                  <span className="type-body text-secondary">{item.note}</span>
                </li>
              ))}
            </ul>

            <div className="mt-6 flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
              <a href={CONTACT_MAILTO} className="btn btn-primary sm:whitespace-nowrap" data-contact-link>
                {CONTACT.button}
                <ArrowGlyph />
              </a>
              <span className="type-small text-muted">{CONTACT.opens}</span>
            </div>

            <p className="type-body mt-6 text-secondary">
              {CONTACT.addressLabel}{" "}
              <a
                href={`mailto:${CONTACT.email}`}
                className="type-code text-link font-medium select-all [overflow-wrap:anywhere]"
                data-contact-address
              >
                {CONTACT.email}
              </a>
            </p>
            <p className="type-small mt-4 text-muted">{CONTACT.privacyNote}</p>
          </div>
        </div>
      </div>
    </Section>
  );
}
