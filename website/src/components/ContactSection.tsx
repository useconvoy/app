import { CONTACT } from "@/content/homepage";
import { getDeliveryConfig } from "@/lib/contact/delivery";
import { ContactForm } from "./ContactForm";
import { Section, SectionHeading } from "./Section";

/** Plain invitation beside the intake form. 5/7 on desktop, one column below 900px. */
export function ContactSection() {
  const delivery = getDeliveryConfig();
  return (
    <Section id={CONTACT.id} labelledBy="contact-heading">
      <div className="grid gap-10 md:grid-cols-12 md:gap-8">
        <div className="md:col-span-5">
          <SectionHeading id="contact-heading" eyebrow={CONTACT.eyebrow} heading={CONTACT.heading} lead={CONTACT.lead} />
        </div>
        <div className="md:col-span-7">
          <ContactForm deliveryAvailable={delivery.available} />
        </div>
      </div>
    </Section>
  );
}
