import type { Metadata } from "next";

import { routines } from "@/lib/fixtures/world";
import { money } from "@/lib/format";

import { CtaBand } from "../components/cta-band";
import { PageIntro } from "../components/page-intro";
import { StepList, type Step } from "../components/step-list";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/solutions" },
  title: "Solutions",
  description:
    "Three routines teams hand to Convoy: quarterly access reviews, vendor due diligence, and policy attestations.",
};

/** Plain names for the systems each routine connects to. */
const SYSTEM_LABELS: Record<string, string> = {
  identity_provider: "Identity provider",
  hris: "HR system",
  document_store: "Document store",
  messaging: "Messaging",
  crm: "CRM",
};

/** Story copy and checkpoint placement, keyed by fixture routine id. */
const STORIES: Record<
  string,
  { kicker: string; narrative: string[]; heldStep: number; heldNote: string }
> = {
  "routine-access-review": {
    kicker: "Access reviews",
    narrative: [
      "Every quarter the same grind: pull the list of who can reach which systems, compare it to HR records, write up the differences, and chase everyone who has not signed off. It has to be right, and it always lands on the same few people.",
      "Convoy runs the review end to end. It gathers both lists, notes every difference, and drafts an exception memo for each one. The memos wait at a checkpoint for a person to judge, because that judgment is the review.",
    ],
    heldStep: 2,
    heldNote:
      "Each exception memo waits here for a named reviewer to sign off before the run continues.",
  },
  "routine-vendor-check": {
    kicker: "Vendor due diligence",
    narrative: [
      "Vendor files go stale quietly. Certificates lapse, insurance documents age out, and nobody notices until a renewal or an audit forces the question.",
      "Convoy keeps the shelf current. It works from your vendor records, requests fresh documents, checks what comes back for completeness, and files everything where it belongs. It works the same whether your vendor records live in one CRM or another, so changing tools does not mean rebuilding the routine.",
    ],
    heldStep: 2,
    heldNote:
      "Documents that come back incomplete are held for a person to decide: accept, chase again, or escalate.",
  },
  "routine-attestation-chase": {
    kicker: "Policy attestations",
    narrative: [
      "Getting a whole company to confirm they have read the updated policy is not hard work, it is just endless: reminders, tallies, and a spreadsheet someone has to keep honest.",
      "Convoy takes the chase. It tracks who is outstanding, sends the reminders you approved, records confirmations as they arrive, and hands you a clean summary of who is still missing. In rehearsal you read every reminder before a single one is sent.",
    ],
    heldStep: 1,
    heldNote:
      "The reminder batch waits here so a person can read the messages before any of them go out.",
  },
};

export default function SolutionsPage() {
  return (
    <>
      <PageIntro
        kicker="Solutions"
        title="Routines teams hand to Convoy"
        lede="Three pieces of recurring work our design partners run today. Each one rehearses safely, stops for human judgment, and leaves a record built for audit."
      />

      <div>
        {routines.map((routine) => {
          const story = STORIES[routine.id];
          const steps: Step[] = routine.planSteps.map(
            (label, index): Step => {
              if (story && index < story.heldStep)
                return { label, state: "done" };
              if (story && index === story.heldStep) {
                return { label, state: "held", note: story.heldNote };
              }
              return { label, state: "queued" };
            },
          );

          return (
            <section
              key={routine.id}
              aria-labelledby={`${routine.id}-heading`}
              className="border-t border-line"
            >
              <div className="mx-auto grid max-w-6xl items-start gap-12 px-6 py-16 lg:grid-cols-2">
                <div>
                  <p className="font-mono text-xs tracking-widest text-muted uppercase">
                    {story?.kicker ?? "Routine"}
                  </p>
                  <h2
                    id={`${routine.id}-heading`}
                    className="mt-3 font-display text-2xl font-medium text-ink"
                  >
                    {routine.name}
                  </h2>
                  <p className="mt-3 text-muted">{routine.descriptor}</p>
                  {story?.narrative.map((paragraph) => (
                    <p
                      key={paragraph.slice(0, 32)}
                      className="mt-4 leading-relaxed text-muted"
                    >
                      {paragraph}
                    </p>
                  ))}
                  <dl className="mt-6 space-y-2 text-sm">
                    <div className="flex gap-2">
                      <dt className="text-muted">Works in:</dt>
                      <dd className="text-ink">
                        {routine.systems
                          .map((system) => SYSTEM_LABELS[system] ?? system)
                          .join(", ")}
                      </dd>
                    </div>
                    <div className="flex gap-2">
                      <dt className="text-muted">Budget:</dt>
                      <dd className="font-mono text-ink">
                        Up to {money(routine.budgetCapUsd)} per run
                      </dd>
                    </div>
                  </dl>
                </div>
                <div className="rounded-lg border border-line bg-card p-8">
                  <p className="mb-6 text-sm font-semibold text-ink">
                    How a run unfolds
                  </p>
                  <StepList steps={steps} />
                </div>
              </div>
            </section>
          );
        })}
      </div>

      <CtaBand
        title="Have a routine like these?"
        body="If your team repeats careful work on a calendar, it is probably a routine. Tell us about it and we will show you how it would run."
      />
    </>
  );
}
