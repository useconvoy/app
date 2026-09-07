import { WORKFLOW } from "@/content/homepage";
import { Disclosure } from "./Disclosure";
import { Section, SectionHeading } from "./Section";

/**
 * Package, Qualify, Release as three rule-topped columns, with one sentence
 * on the release identity that carries through them. Each step can expand
 * into what goes in and what comes out. Three columns from 900px, a
 * numbered vertical process below.
 */
export function ReleaseWorkflow() {
  return (
    <Section id={WORKFLOW.id} labelledBy="workflow-heading">
      <SectionHeading id="workflow-heading" eyebrow={WORKFLOW.eyebrow} heading={WORKFLOW.heading} lead={WORKFLOW.lead} />

      <ol className="mt-8 grid gap-7 md:grid-cols-3 md:gap-8" aria-label="Release workflow steps">
        {WORKFLOW.stages.map((stage) => (
          <li key={stage.number} className="flex min-w-0 flex-col border-t border-strong pt-5">
            <p className="font-mono text-sm font-medium tracking-[0.04em] text-accent">{stage.number}</p>
            <h3 className="type-h3 mt-2 text-primary">{stage.title}</h3>
            <p className="type-body mt-2 mb-3 text-secondary">{stage.body}</p>
            <Disclosure summary={WORKFLOW.detailLabel(stage.title)} summaryClassName="type-label text-primary" className="mt-auto border-t border-subtle">
              <dl className="grid gap-3 sm:grid-cols-2 md:grid-cols-1">
                {(["in", "out"] as const).map((direction) => (
                  <div key={direction}>
                    <dt className="type-eyebrow">{direction === "in" ? "In" : "Out"}</dt>
                    <dd className="mt-1">
                      <ul className="flex flex-col gap-1.5 type-small">
                        {stage.detail[direction].map((item) => (
                          <li key={item} className="flex items-baseline gap-3">
                            <span aria-hidden="true" className="inline-block w-3 flex-none -translate-y-1 border-t border-strong" />
                            {item}
                          </li>
                        ))}
                      </ul>
                    </dd>
                  </div>
                ))}
              </dl>
            </Disclosure>
          </li>
        ))}
      </ol>
    </Section>
  );
}
