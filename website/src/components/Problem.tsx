import { PROBLEM } from "@/content/homepage";
import { ReleaseEnvelope } from "./ReleaseEnvelope";
import { Section, SectionHeading } from "./Section";

/**
 * One plain-language lead and four aligned dependency rows under a single
 * mono column label. The compact envelope beneath the lead lights up the
 * field groups those rows map onto, so the section reads as the release
 * object opening up rather than four tiles.
 */
export function Problem() {
  return (
    <Section id={PROBLEM.id} labelledBy="problem-heading">
      <div className="grid gap-10 md:grid-cols-12 md:gap-8">
        <div className="md:col-span-5">
          <SectionHeading id="problem-heading" eyebrow={PROBLEM.eyebrow} heading={PROBLEM.heading} lead={PROBLEM.lead} />
          <div className="prose-measure mt-6 space-y-4 text-secondary type-body">
            {PROBLEM.body.map((paragraph) => (
              <p key={paragraph}>{paragraph}</p>
            ))}
          </div>
          <div className="mt-8 hidden md:block">
            <ReleaseEnvelope highlight={["processing", "runtime", "evidence"]} compact />
          </div>
        </div>

        <div className="md:col-span-7 md:pt-2">
          <div className="grid grid-cols-[minmax(160px,3fr)_9fr] gap-6 border-t border-strong py-3 font-mono text-[13px] uppercase tracking-[0.06em] text-muted max-sm:hidden">
            <span aria-hidden="true" />
            <span>{PROBLEM.columnLabel}</span>
          </div>
          <ol className="border-t border-strong sm:border-t-0" aria-label={PROBLEM.columnLabel}>
            {PROBLEM.dependencies.map((item, index) => (
              <li
                key={item.label}
                className="grid gap-3 border-b border-subtle py-6 sm:grid-cols-[minmax(160px,3fr)_9fr] sm:gap-6 sm:border-t sm:border-b-0 last:sm:border-b"
              >
                <h3 className="flex items-baseline gap-3 type-body font-medium text-primary">
                  <span className="font-mono text-[13px] font-medium text-muted" aria-hidden="true">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                  {item.label}
                </h3>
                <p className="type-body text-secondary">{item.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </Section>
  );
}
