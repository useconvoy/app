import type { Metadata } from "next";

import { CtaBand } from "../components/cta-band";
import { PageIntro } from "../components/page-intro";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/platform" },
  title: "Platform",
  description:
    "How Convoy works: routines, workspaces and systems, rehearsal copies, checkpoints, budgets, and evidence export.",
};

const CHECKPOINT_KINDS = [
  {
    name: "Approve the plan",
    body: "Before a run starts on anything real, the plan is laid out as plain sentences for a named person to approve, edit, or send back.",
  },
  {
    name: "Answer a question",
    body: "Mid-run, a routine can stop and ask. Does this exception stand? Is this document acceptable? The run waits for the answer, and the answer is recorded with the name of the person who gave it.",
  },
  {
    name: "Resume a pause",
    body: "Anyone with the right role can pause a run at any moment. It resumes only when a person says so, and both moments land in the record.",
  },
] as const;

function Section({
  id,
  kicker,
  title,
  children,
}: {
  id: string;
  kicker: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="border-t border-line">
      <div className="mx-auto grid max-w-6xl gap-8 px-6 py-16 lg:grid-cols-[1fr_2fr]">
        <div>
          <p className="font-mono text-xs tracking-widest text-muted uppercase">
            {kicker}
          </p>
          <h2
            id={`${id}-heading`}
            className="mt-3 font-display text-2xl font-medium text-ink"
          >
            {title}
          </h2>
        </div>
        <div className="max-w-2xl space-y-4 leading-relaxed text-muted">
          {children}
        </div>
      </div>
    </section>
  );
}

export default function PlatformPage() {
  return (
    <>
      <PageIntro
        kicker="Platform"
        title="How Convoy works"
        lede="Six ideas carry the whole product: routines, workspaces, rehearsal, checkpoints, budgets, and a record you can hand to anyone."
      />

      <Section id="routines" kicker="The unit of work" title="Routines">
        <p>
          A routine is a piece of recurring work described in plain language:
          what it does, which systems it touches, where it stops for people,
          and what it may spend. The quarterly access review is a routine. So
          is the vendor document refresh.
        </p>
        <p>
          A routine can run on a schedule, on demand, or when something
          happens in a connected system. Its history, its plan, its test
          scores, and everyone&rsquo;s feedback on it live in one place, so
          you can open a routine and see everything about it today.
        </p>
      </Section>

      <Section
        id="workspaces"
        kicker="Where work happens"
        title="Workspaces and systems"
      >
        <p>
          A workspace is the set of systems a routine works in: your identity
          provider, your HR system, your document store, your messaging. Each
          system is connected once, managed in one place, and granted to
          routines with an explicit level of access.
        </p>
        <p>
          Grants are two words long: <strong className="text-ink">View only</strong> or{" "}
          <strong className="text-ink">Can update</strong>. A routine that only needs to
          read your HR records never gets the power to change them, and you
          can see every grant on one screen.
        </p>
      </Section>

      <Section
        id="rehearsal"
        kicker="Practice before production"
        title="Rehearsal copies"
      >
        <p>
          Every workspace comes with a rehearsal copy: the same systems, the
          same shape of data, none of the consequences. In rehearsal, emails
          collect in an outbox that never sends and record changes land on the
          copy, so you can read exactly what a routine would have done before
          it ever does it.
        </p>
        <p>
          Rehearsal also owns the clock. A review that chases sign-offs across
          two weeks can be fast-forwarded through those weeks in minutes, so
          you watch the whole story, reminders and all, in one sitting.
          Rehearsal runs are always clearly marked; nothing about them is ever
          confused with production.
        </p>
      </Section>

      <Section id="checkpoints" kicker="Where people decide" title="Checkpoints">
        <p>
          A checkpoint is a moment where a run stops and waits for a person.
          There are three kinds, and each one is a distinct, recorded act:
        </p>
        <ul className="space-y-4">
          {CHECKPOINT_KINDS.map((kind) => (
            <li
              key={kind.name}
              className="rounded-lg border border-line bg-card p-5"
            >
              <p className="font-semibold text-ink">{kind.name}</p>
              <p className="mt-2 text-sm leading-relaxed">{kind.body}</p>
            </li>
          ))}
        </ul>
        <p>
          Checkpoints are assigned to people or teams, carry deadlines, and
          say up front what happens if nobody answers in time. Held items
          appear in one inbox so nothing waits in silence.
        </p>
      </Section>

      <Section id="budgets" kicker="Spending, capped" title="Budgets">
        <p>
          Every run carries a spending cap you set per routine, such as up to
          $75 per run for the access review. While a run is moving you see
          three numbers: the cap, what is spent, and what is set aside for
          work in flight.
        </p>
        <p>
          As spending nears the cap you get a clear warning. At the cap, the
          run stops and holds for a person. There is no mode where a routine
          quietly keeps spending.
        </p>
      </Section>

      <Section
        id="evidence"
        kicker="Proof on demand"
        title="Evidence export"
      >
        <p>
          Every run keeps its record: each step, each decision, each file it
          produced, and the named person behind every judgment call. When an
          auditor asks, you export an evidence binder from any run: a single
          archive with the files, a manifest, and checksums.
        </p>
        <p>
          The binder is built for handing over as-is. No screenshots, no
          reconstruction after the fact, no asking engineering for a favor.
        </p>
      </Section>

      {/* This page is the platform, so the default secondary link would send
          the reader back to where they already are. */}
      <CtaBand
        secondary={{
          href: "/solutions",
          label: "See what teams hand over",
        }}
      />
    </>
  );
}
