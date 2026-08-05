import type { Metadata } from "next";
import Link from "next/link";

import { people, routines } from "@/lib/fixtures/world";
import { money } from "@/lib/format";

import { CtaBand } from "./components/cta-band";
import { StepList, type Step } from "./components/step-list";

export const metadata: Metadata = {
  title: { absolute: "Convoy: routine work, done carefully" },
  description:
    "Convoy runs your recurring routines, rehearses them safely first, and holds for your judgment at every step that matters.",
};

const HOW_IT_WORKS = [
  {
    number: "01",
    title: "Rehearse safely",
    body: "Every routine runs first against a rehearsal copy of your workspace. Emails land in an outbox that never sends, records change on a copy, and the clock can fast-forward through days in minutes.",
  },
  {
    number: "02",
    title: "Approve the plan",
    body: "Before a routine touches anything real, you see its plan as a list of plain sentences. You approve it, edit it, or send it back. Nothing starts on a shrug.",
  },
  {
    number: "03",
    title: "It holds for your judgment",
    body: "When a run reaches a decision that belongs to a person, it stops and asks. The checkpoint names who it is waiting for, what it needs, and what happens if nobody answers.",
  },
] as const;

const TRUST_POINTS = [
  {
    title: "Every action recorded",
    body: "Each step a routine takes is written down with who or what did it and when. The record is built for handing to an auditor, not for us.",
  },
  {
    title: "Budgets on every run",
    body: "Every run carries a spending cap you set. You see what is spent and what is set aside as it happens, and a run that reaches its cap stops.",
  },
  {
    title: "Nothing live without rehearsal",
    body: "A routine earns its way to production by showing you rehearsal results first. You review exactly what it would have done before anything goes out.",
  },
] as const;

export default function HomePage() {
  const accessReview = routines[0];
  const reviewer = people[0];
  const operator = people[1];

  const narrativeSteps: Step[] = (accessReview?.planSteps ?? []).map(
    (label, index): Step => {
      if (index < 2) return { label, state: "done" };
      if (index === 2) {
        return {
          label,
          state: "held",
          note: "Checkpoint: the exception memos wait here until a person reads them and signs off. The run does not move until someone does.",
        };
      }
      return { label, state: "queued" };
    },
  );

  return (
    <>
      {/* Hero */}
      <section className="mx-auto max-w-4xl px-6 pt-20 pb-16 text-center sm:pt-28">
        <h1 className="font-display text-5xl leading-tight font-medium text-ink sm:text-6xl">
          Routine work, done carefully
        </h1>
        <p className="mx-auto mt-6 max-w-2xl text-lg text-muted">
          Convoy runs the routines your team repeats every month and every
          quarter. It rehearses each one safely before anything is real, and it
          holds for your judgment at every step that matters.
        </p>
        <div className="mt-10 flex flex-wrap justify-center gap-4">
          <Link
            href="/demo"
            className="inline-flex items-center rounded-md bg-pine px-6 py-3 text-sm font-medium text-card hover:bg-pine-deep"
          >
            Request a demo
          </Link>
          <a
            href="#how-it-works"
            className="inline-flex items-center rounded-md border border-line bg-card px-6 py-3 text-sm font-medium text-ink hover:border-muted"
          >
            See how it works
          </a>
        </div>
      </section>

      {/* How it works */}
      <section
        id="how-it-works"
        aria-labelledby="how-it-works-heading"
        className="border-t border-line bg-card"
      >
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2
            id="how-it-works-heading"
            className="text-center font-display text-3xl font-medium text-ink"
          >
            How a routine earns your trust
          </h2>
          <div className="mt-12 grid gap-6 md:grid-cols-3">
            {HOW_IT_WORKS.map((item) => (
              <article
                key={item.number}
                className="rounded-lg border border-line bg-field p-6"
              >
                <p className="font-mono text-xs tracking-widest text-muted">
                  {item.number}
                </p>
                <h3 className="mt-3 text-lg font-semibold text-ink">
                  {item.title}
                </h3>
                <p className="mt-3 text-sm leading-relaxed text-muted">
                  {item.body}
                </p>
              </article>
            ))}
          </div>
        </div>
      </section>

      {/* Product narrative: the quarterly access review */}
      <section
        aria-labelledby="narrative-heading"
        className="border-t border-line"
      >
        <div className="mx-auto max-w-6xl px-6 py-20">
          <div className="grid items-start gap-12 lg:grid-cols-2">
            <div>
              <p className="font-mono text-xs tracking-widest text-muted uppercase">
                A real routine
              </p>
              <h2
                id="narrative-heading"
                className="mt-4 font-display text-3xl font-medium text-ink"
              >
                The quarterly access review, handled
              </h2>
              <p className="mt-5 leading-relaxed text-muted">
                Every quarter, someone on your team pulls the list of who can
                reach which systems, compares it against HR records, writes up
                every difference, and chases the people who never reply. It
                takes days and it has to be right.
              </p>
              <p className="mt-4 leading-relaxed text-muted">
                Convoy runs the same review as a routine. It gathers the lists,
                notes the differences, and drafts an exception memo for each
                one. Then it stops, because judging an exception is a decision
                that belongs to a person.
              </p>
              <p className="mt-4 leading-relaxed text-muted">
                In rehearsal, the whole review runs against a copy of your
                workspace, and the reminder emails collect in an outbox that
                never sends. You see exactly what it would have sent before
                anything goes out, word for word, name by name.
              </p>
              <p className="mt-6 font-mono text-sm text-muted">
                Up to {money(accessReview?.budgetCapUsd ?? 75)} per run, and
                not a cent past it.
              </p>
            </div>
            <div className="rounded-lg border border-line bg-card p-8">
              <p className="text-sm font-semibold text-ink">
                {accessReview?.name ?? "Quarterly user access review"}
              </p>
              <p className="mt-1 mb-6 text-sm text-muted">
                {accessReview?.descriptor}
              </p>
              <StepList steps={narrativeSteps} />
            </div>
          </div>
        </div>
      </section>

      {/* Trust band */}
      <section
        aria-labelledby="trust-heading"
        className="bg-pine-deep text-card"
      >
        <div className="mx-auto max-w-6xl px-6 py-20">
          <h2
            id="trust-heading"
            className="text-center font-display text-3xl font-medium"
          >
            Careful is the whole product
          </h2>
          <div className="mt-12 grid gap-10 md:grid-cols-3">
            {TRUST_POINTS.map((point) => (
              <div key={point.title}>
                <h3 className="text-lg font-semibold">{point.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-card/75">
                  {point.body}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Testimonials (design-partner placeholders, generic names only) */}
      <section aria-labelledby="voices-heading" className="border-t border-line">
        <div className="mx-auto max-w-5xl px-6 py-20">
          <h2
            id="voices-heading"
            className="text-center font-display text-3xl font-medium text-ink"
          >
            What careful teams say
          </h2>
          <div className="mt-12 grid gap-6 md:grid-cols-2">
            <figure className="rounded-lg border border-line bg-card p-8">
              <blockquote className="leading-relaxed text-ink">
                &ldquo;The first time I watched the outbox review, I stopped
                worrying. I could read every email it wanted to send before a
                single one went out.&rdquo;
              </blockquote>
              <figcaption className="mt-5 text-sm text-muted">
                {reviewer?.name ?? "J. Doe"}, compliance lead at a design
                partner
              </figcaption>
            </figure>
            <figure className="rounded-lg border border-line bg-card p-8">
              <blockquote className="leading-relaxed text-ink">
                &ldquo;Our access review used to eat a week each quarter. Now
                it comes to me twice: once to approve the plan, once to sign
                the exceptions.&rdquo;
              </blockquote>
              <figcaption className="mt-5 text-sm text-muted">
                {operator?.name ?? "R. Roe"}, operations lead at a design
                partner
              </figcaption>
            </figure>
          </div>
        </div>
      </section>

      <CtaBand />
    </>
  );
}
