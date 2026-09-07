import { FAQ as COPY } from "@/content/homepage";
import { Disclosure } from "./Disclosure";
import { Section, SectionHeading } from "./Section";

/** Six boundaries: heading on the left, native disclosures on the right, the first open. */
export function FAQ() {
  return (
    <Section id={COPY.id} labelledBy="faq-heading">
      <div className="grid gap-6 md:grid-cols-12 md:gap-10">
        <div className="md:col-span-5">
          <SectionHeading id="faq-heading" eyebrow={COPY.eyebrow} heading={COPY.heading} />
        </div>
        <div className="border-t border-subtle md:col-span-7">
          {COPY.items.map((item, index) => (
            <Disclosure
              key={item.question}
              summary={<h3 className="type-lead font-medium">{item.question}</h3>}
              open={index === 0}
              className="border-b border-subtle"
              summaryClassName="text-primary"
            >
              <p className="type-body prose-measure">{item.answer}</p>
            </Disclosure>
          ))}
        </div>
      </div>
    </Section>
  );
}
