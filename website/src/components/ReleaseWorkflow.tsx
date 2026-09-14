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

      <ol className="workflow-steps" aria-label="Release workflow steps">
        {WORKFLOW.stages.map((stage) => (
          <li key={stage.number} className="workflow-card">
            <div className="workflow-card-art" aria-hidden="true"><span>{stage.number}</span><WorkflowGlyph stage={stage.number} /></div>
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

/** Static drafting symbols: a bundle, evaluation sheet, and target compute. */
function WorkflowGlyph({ stage }: { stage: string }) {
  return (
    <svg viewBox="0 0 112 80" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true" focusable="false">
      {stage === "01" ? <><path d="m28 25 27-14 29 14v31L56 71 28 56Zm0 0 28 15 28-15M56 40v31M42 18l28 15v13" /><path d="m14 36 8 4m69 0 8-4M56 2v4" opacity=".4" /></> : stage === "02" ? <><path d="M31 9h39l12 12v50H31ZM70 9v14h12M44 34h8m8 0h10M44 46h8m8 0h10M44 58h8m8 0h10" /><path d="M23 17h-6v45h6M90 29h6v33h-6" opacity=".4" /></> : <><rect x="33" y="17" width="46" height="46" rx="2" /><rect x="44" y="28" width="24" height="24" rx="1" /><path d="M43 8v9m13-9v9m13-9v9M43 63v9m13-9v9m13-9v9M24 27h9m-9 13h9m-9 13h9m46-26h9m-9 13h9m-9 13h9" /><path d="m51 40 4 4 7-9" /></>}
    </svg>
  );
}
