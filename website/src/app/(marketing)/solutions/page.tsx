import type { Metadata } from "next";

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

/**
 * Three worked examples of routines teams hand over. This is marketing
 * narrative, owned by this page: the console shows each organization its
 * own routines, and nothing here leaks into the product.
 */
interface ExampleRoutine {
  id: string;
  kicker: string;
  name: string;
  descriptor: string;
  narrative: string[];
  planSteps: string[];
  heldStep: number;
  heldNote: string;
  systems: string[];
  budgetCapUsd: number;
}

const EXAMPLES: ExampleRoutine[] = [
  {
    id: "example-access-review",
    kicker: "Access reviews",
    name: "Quarterly user access review",
    descriptor: "Looks up people and their access, reconciles differences, and chases sign-offs.",
    narrative: [
      "Every quarter the same grind: pull the list of who can reach which systems, compare it to HR records, write up the differences, and chase everyone who has not signed off. It has to be right, and it always lands on the same few people.",
      "Convoy runs the review end to end. It gathers both lists, notes every difference, and drafts an exception memo for each one. The memos wait at a checkpoint for a person to judge, because that judgment is the review.",
    ],
    planSteps: [
      "Pull the current list of people and their access",
      "Compare against the HR system and note differences",
      "Write an exception memo for each difference",
      "Chase anyone who has not responded",
      "Assemble the final review packet",
    ],
    heldStep: 2,
    heldNote:
      "Each exception memo waits here for a named reviewer to sign off before the run continues.",
    systems: ["Identity provider", "HR system", "Document store", "Messaging"],
    budgetCapUsd: 75,
  },
  {
    id: "example-vendor-check",
    kicker: "Vendor due diligence",
    name: "Vendor due-diligence document refresh",
    descriptor: "Collects updated documents from vendors and files them where they belong.",
    narrative: [
      "Vendor files go stale quietly. Certificates lapse, insurance documents age out, and nobody notices until a renewal or an audit forces the question.",
      "Convoy keeps the shelf current. It works from your vendor records, requests fresh documents, checks what comes back for completeness, and files everything where it belongs. It works the same whether your vendor records live in one CRM or another, so changing tools does not mean rebuilding the routine.",
    ],
    planSteps: [
      "List vendors whose documents are due for refresh",
      "Request updated documents from each vendor",
      "Check received documents for completeness",
      "File documents and update vendor records",
    ],
    heldStep: 2,
    heldNote:
      "Documents that come back incomplete are held for a person to decide: accept, chase again, or escalate.",
    systems: ["CRM", "Document store", "Messaging"],
    budgetCapUsd: 40,
  },
  {
    id: "example-attestation-chase",
    kicker: "Policy attestations",
    name: "Policy attestation chase",
    descriptor: "Reminds people to confirm they have read the policies, and keeps score.",
    narrative: [
      "Getting a whole company to confirm they have read the updated policy is not hard work, it is just endless: reminders, tallies, and a spreadsheet someone has to keep honest.",
      "Convoy takes the chase. It tracks who is outstanding, sends the reminders you approved, records confirmations as they arrive, and hands you a clean summary of who is still missing. In rehearsal you read every reminder before a single one is sent.",
    ],
    planSteps: [
      "List people with outstanding attestations",
      "Send a reminder to each person",
      "Record confirmations as they arrive",
      "Summarize who is still outstanding",
    ],
    heldStep: 1,
    heldNote:
      "The reminder batch waits here so a person can read the messages before any of them go out.",
    systems: ["Messaging", "Document store"],
    budgetCapUsd: 30,
  },
];

export default function SolutionsPage() {
  return (
    <>
      <PageIntro
        kicker="Solutions"
        title="Routines teams hand to Convoy"
        lede="Three pieces of recurring work our design partners run today. Each one rehearses safely, stops for human judgment, and leaves a record built for audit."
      />

      <div>
        {EXAMPLES.map((routine) => {
          const steps: Step[] = routine.planSteps.map(
            (label, index): Step => {
              if (index < routine.heldStep) return { label, state: "done" };
              if (index === routine.heldStep) {
                return { label, state: "held", note: routine.heldNote };
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
                    {routine.kicker}
                  </p>
                  <h2
                    id={`${routine.id}-heading`}
                    className="mt-3 font-display text-2xl font-medium text-ink"
                  >
                    {routine.name}
                  </h2>
                  <p className="mt-3 text-muted">{routine.descriptor}</p>
                  {routine.narrative.map((paragraph) => (
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
                      <dd className="text-ink">{routine.systems.join(", ")}</dd>
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
