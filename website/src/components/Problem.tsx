import { PROBLEM } from "@/content/homepage";
import { Section, SectionHeading } from "./Section";

/** One heading, one lead, four concise dependency rows. */
export function Problem() {
  return (
    <Section id={PROBLEM.id} labelledBy="problem-heading">
      <div className="grid gap-8 md:grid-cols-12 md:gap-10">
        <div className="md:col-span-5">
          <SectionHeading id="problem-heading" eyebrow={PROBLEM.eyebrow} heading={PROBLEM.heading} lead={PROBLEM.lead} />
        </div>
        <ol className="border-t border-strong md:col-span-7 md:mt-2" aria-label={PROBLEM.columnLabel}>
          {PROBLEM.dependencies.map((item, index) => (
            <li key={item.label} className="grid gap-1 border-b border-subtle py-3.5 sm:grid-cols-[minmax(150px,3fr)_9fr] sm:gap-6 sm:py-5">
              <h3 className="flex items-baseline gap-3 type-body font-medium text-primary">
                <span className="font-mono text-sm font-medium text-muted" aria-hidden="true">
                  {String(index + 1).padStart(2, "0")}
                </span>
                {item.label}
              </h3>
              <p className="type-body text-secondary">{item.body}</p>
            </li>
          ))}
        </ol>
      </div>
    </Section>
  );
}
