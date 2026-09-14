import { CONTACT, CONTACT_MAILTO } from "@/content/homepage";
import { ArrowGlyph } from "./Hero";
import { Section, SectionHeading } from "./Section";

const [CONTACT_LOCAL, CONTACT_DOMAIN] = CONTACT.email.split("@");

/**
 * The contact block, action first: the mailto link that opens the visitor's
 * own mail app with the subject and a three-line template filled in, the
 * address as selectable text beside it, then a compact note of what is
 * helpful to include. Nothing is collected or stored on the site. The
 * address is one constant in src/content/homepage.ts, overridable with
 * NEXT_PUBLIC_CONTACT_EMAIL.
 */
export function ContactSection() {
  return (
    <Section id={CONTACT.id} labelledBy="contact-heading">
      <div className="contact-layout">
        <div>
          <SectionHeading id="contact-heading" eyebrow={CONTACT.eyebrow} heading={CONTACT.heading} lead={CONTACT.lead} />
        </div>
        <div className="contact-panel">
          <div>
            <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
              <a href={CONTACT_MAILTO} className="btn btn-primary sm:whitespace-nowrap" data-contact-link>
                {CONTACT.button}
                <ArrowGlyph />
              </a>
              <span className="type-small text-muted">{CONTACT.opens}</span>
            </div>

            <div className="mt-5">
              <p className="type-eyebrow">{CONTACT.addressLabel}</p>
              {/* On a narrow screen the address wraps before the @ (the only
                  break opportunity at normal text sizes) and keeps the domain
                  whole; only under heavy text enlargement may the domain itself
                  wrap rather than overflow. The text content and the href stay
                  one contiguous address, so copying or selecting it is unaffected. */}
              <a
                href={`mailto:${CONTACT.email}`}
                className="mt-1 inline-block font-mono text-[1.375rem] leading-tight tracking-[-0.01em] text-primary underline decoration-accent decoration-2 underline-offset-[6px] select-all [overflow-wrap:anywhere] hover:text-accent-hover sm:text-[1.625rem]"
                data-contact-address
              >
                {CONTACT_LOCAL}
                <wbr />
                <span data-contact-domain>
                  @{CONTACT_DOMAIN}
                </span>
              </a>
            </div>

            <div className="mt-6 border-t border-subtle pt-4">
              <p className="type-eyebrow">{CONTACT.guidanceIntro}</p>
              <ul className="mt-2 flex flex-col gap-1.5">
                {CONTACT.guidance.map((item) => (
                  <li key={item.label} className="type-body text-secondary">
                    <span className="font-medium text-primary">{item.label}</span>
                    <span aria-hidden="true"> · </span>
                    <span className="sr-only">: </span>
                    {item.note}
                  </li>
                ))}
              </ul>
            </div>
            <p className="type-small mt-4 text-muted">{CONTACT.privacyNote}</p>
          </div>
        </div>
      </div>
    </Section>
  );
}
