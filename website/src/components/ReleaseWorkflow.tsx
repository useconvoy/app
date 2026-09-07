import { WORKFLOW } from "@/content/homepage";
import { Disclosure } from "./Disclosure";
import { ReleaseEnvelope } from "./ReleaseEnvelope";
import { Section, SectionHeading } from "./Section";

/**
 * Package, Qualify, Release as three rule-topped columns, each stage tagged
 * proposed. The envelope strip above carries the same version tag across
 * all three; each stage can expand into what goes in and what comes out.
 * Three columns from 900px, a numbered vertical process below.
 */
export function ReleaseWorkflow() {
  return (
    <Section id={WORKFLOW.id} labelledBy="workflow-heading">
      <SectionHeading
        id="workflow-heading"
        eyebrow={WORKFLOW.eyebrow}
        heading={WORKFLOW.heading}
        lead={
          <>
            {WORKFLOW.lead}{" "}
            <span className="ml-1 inline-flex items-center gap-2 rounded-sm bg-warning-tint px-3 py-1 align-middle font-mono text-[13px] font-medium text-warning">
              <span aria-hidden="true" className="h-2 w-2 rounded-full bg-current" />
              {WORKFLOW.badge}
            </span>
          </>
        }
      />

      <div className="mt-10">
        <ReleaseEnvelope compact />
        <p className="type-small mt-2 text-muted">{WORKFLOW.identityNote}</p>
      </div>

      <ol className="mt-10 grid gap-8 md:grid-cols-3 md:gap-8" aria-label="Release workflow stages">
        {WORKFLOW.stages.map((stage) => (
          <li key={stage.number} className="flex min-w-0 flex-col border-t border-strong pt-6">
            <p className="font-mono text-[14px] font-medium tracking-[0.04em] text-accent">
              {stage.number} <span className="text-muted">·</span> {WORKFLOW.stageTag}
            </p>
            <h3 className="type-h3 mt-3 text-primary">{stage.title}</h3>
            <p className="type-body mt-3 mb-4 text-secondary">{stage.body}</p>
            <Disclosure summary={WORKFLOW.detailLabel} summaryClassName="type-label text-primary" className="mt-auto border-t border-subtle pt-1">
              <dl className="grid gap-3 sm:grid-cols-2 md:grid-cols-1">
                {(["in", "out"] as const).map((direction) => (
                  <div key={direction}>
                    <dt className="type-eyebrow">{direction === "in" ? "In" : "Out"}</dt>
                    <dd className="mt-1">
                      <ul className="flex flex-col gap-2 type-small">
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

      <p className="type-small mt-10 max-w-[820px] border-l-2 border-warning pl-4 text-secondary">{WORKFLOW.disclaimer}</p>
    </Section>
  );
}
