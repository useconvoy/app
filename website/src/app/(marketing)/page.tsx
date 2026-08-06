import type { Metadata } from "next";

import { CtaBand } from "./components/cta-band";
import { Hero } from "./components/hero";
import { ImprovementLoop } from "./components/improvement-loop";
import { Reveal } from "./components/reveal";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/" },
  title: { absolute: "Convoy Labs: routine work, done carefully" },
  description:
    "Convoy runs the routines your team repeats every close and every quarter. Each one rehearses safely first, holds for your sign-off, and leaves a record built for an auditor.",
};

/* The work these teams actually own. The highest-intent words on the site, so
 * they are real text rather than an image. */
const WORK = [
  "Quarterly access reviews",
  "SOC 2 and ISO evidence collection",
  "Control testing and workpapers",
  "Vendor due-diligence refresh",
  "Policy attestation chases",
  "Audit request fulfillment",
];

const PHASES = [
  {
    number: "01",
    title: "Plan",
    body: "Before anything runs, the routine writes down what it plans to do: the steps, the systems it will touch, where it will stop for your sign-off, and the most it is allowed to spend. Every change to the plan records who made it and why.",
  },
  {
    number: "02",
    title: "Approve",
    body: "Nothing sensitive happens without you. Read the plan, ask for changes, or approve it. Steps marked as checkpoints pause the run and wait for your sign-off before the work continues.",
  },
  {
    number: "03",
    title: "Execute",
    body: "A run keeps working for as long as the job takes, hours or weeks. If something interrupts it, it picks up where it left off. You can pause it, drop in a note to redirect it, or resume it whenever you like.",
  },
  {
    number: "04",
    title: "Learn",
    body: "When a run finishes, Convoy looks at what went well and what did not, and proposes ways to do the routine better. Each change is tested against past runs and ships only with your approval. Your tenth access review goes smoother than your first.",
  },
];

const PLATFORM = [
  {
    title: "Durable by construction",
    body: "Each run is a durable workflow with a complete event history. Multi-day executions checkpoint automatically, and failures resume instead of restarting from zero.",
  },
  {
    title: "Budgets that hold",
    body: "A per-run dollar cap enforced upstream of every model call. A cap is a cap. Pause, kill, and concurrency limits live in the core loop, not bolted on.",
  },
  {
    title: "Isolated execution",
    body: "Every run works inside its own sealed environment, holding credentials scoped to one system and one run. How far a mistake can reach is a decision someone made, not an accident.",
  },
  {
    title: "An attributed record",
    body: "Every step, every call out to another system, and every file produced carries its own history. Each approval, sign-off, and resume is a separate event with a name on it. Replayable end to end.",
  },
];

const LEDGER = [
  {
    label: "Sign-off",
    value:
      "Held steps require a named approver, recorded with the person and the time",
  },
  {
    label: "Plans",
    value: "Versioned end to end; every revision keeps its author and reason",
  },
  {
    label: "Records",
    value:
      "An event history that cannot be edited; any run replayable step by step",
  },
  {
    label: "Files",
    value: "Checksummed, traceable to the step that made them, ready to export",
  },
  {
    label: "Access",
    value: "Per-system, per-run credentials; least privilege by default",
  },
  {
    label: "Budgets",
    value: "Dollar caps enforced ahead of the work, not counted up afterwards",
  },
  {
    label: "Encryption",
    value: "Run payloads encrypted while the work is in flight",
  },
  { label: "Deployment", value: "Options to match your data boundary" },
];

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-4 block font-mono text-xs font-semibold tracking-[0.17em] text-muted uppercase">
      {children}
    </span>
  );
}

function SectionHeading({
  id,
  children,
}: {
  id: string;
  children: React.ReactNode;
}) {
  return (
    <h2
      id={id}
      className="font-display text-[clamp(30px,3.5vw,44px)] leading-[1.12] font-medium tracking-[-0.012em] text-ink"
    >
      {children}
    </h2>
  );
}

export default function HomePage() {
  return (
    <>
      <Hero />

      {/* The work: split A, copy left, the vocabulary right. */}
      <section
        aria-labelledby="work-heading"
        className="border-t border-line-soft py-16 sm:py-24"
      >
        <div className="mx-auto grid max-w-[1160px] gap-9 px-5 sm:px-7 lg:grid-cols-[1.15fr_0.85fr] lg:gap-[88px]">
          <Reveal>
            <Eyebrow>The work</Eyebrow>
            <SectionHeading id="work-heading">
              Chat cannot close a workpaper.
            </SectionHeading>
            <p className="mt-4 text-[17.5px] text-muted">
              Recurring compliance work like access reviews, evidence
              collection, control testing, and vendor reconciliations stretches
              across days, systems, and deadlines. It does not fit in a chat
              window, and it should not fill your evenings. Convoy gives each
              routine its own run that plans the work, waits for your sign-off,
              carries it across your systems, survives interruptions, and shows
              its work.
            </p>
          </Reveal>
          <Reveal className="flex flex-wrap content-start gap-2.5 pt-1.5">
            {WORK.map((item) => (
              <span
                key={item}
                className="rounded-full border border-line bg-card px-3.5 py-2 font-mono text-[12.5px] tracking-[0.04em] text-ink"
              >
                {item}
              </span>
            ))}
          </Reveal>
        </div>
      </section>

      {/* A ruled strip, the way a tally rule separates blocks on a workpaper.
          Sixteen pixels of it do more for the page's rhythm than another forty
          of padding would. */}
      <div aria-hidden="true" className="register-band" />

      {/* How a run works: split B, the four phases. */}
      <section
        id="lifecycle"
        aria-labelledby="lifecycle-heading"
        className="py-16 sm:py-24"
      >
        <div className="mx-auto grid max-w-[1160px] items-start gap-9 px-5 sm:px-7 lg:grid-cols-[0.7fr_1.45fr] lg:gap-[88px]">
          {/* The heading travels with the column it belongs to. A short title
              beside a tall list otherwise leaves several hundred pixels of
              nothing, and the reader loses what they are reading about. */}
          <Reveal className="lg:sticky lg:top-24 lg:self-start">
            <Eyebrow>How a run works</Eyebrow>
            <SectionHeading id="lifecycle-heading">
              Plan. Approve. Execute.{" "}
              <em className="font-medium italic">Learn.</em>
            </SectionHeading>
          </Reveal>
          <div className="relative">
            <span
              aria-hidden="true"
              className="absolute top-7 bottom-7 left-[19px] w-0.5 bg-line"
            />
            {PHASES.map((phase) => (
              <Reveal key={phase.number}>
                <div className="group relative grid grid-cols-[40px_1fr] gap-x-5 py-6 sm:gap-x-7">
                  <span className="relative z-10 flex h-10 w-10 items-center justify-center rounded-full border-2 border-line bg-card font-mono text-xs font-semibold text-muted transition-[border-color,color] duration-200 ease-[var(--ease-entrance)] group-hover:border-pass group-hover:text-pass-text">
                    {phase.number}
                  </span>
                  <div>
                    <h3 className="pt-1 font-display text-[22px] font-semibold text-ink">
                      {phase.title}
                    </h3>
                    <p className="mt-2 max-w-[60ch] text-base text-muted">
                      {phase.body}
                    </p>
                  </div>
                </div>
              </Reveal>
            ))}
          </div>
        </div>
      </section>

      {/* The platform: a register rather than a third split. Two sections in a
          row with the same column widths read as one long section, so this one
          changes shape as well as subject. */}
      <section
        id="platform"
        aria-labelledby="platform-heading"
        className="border-y border-line bg-card py-16 sm:py-24"
      >
        <div className="mx-auto max-w-[1160px] px-5 sm:px-7">
          <Reveal className="max-w-[40ch]">
            <Eyebrow>The platform</Eyebrow>
            <SectionHeading id="platform-heading">
              Infrastructure for work you can hold accountable.
            </SectionHeading>
          </Reveal>
          {/* The rules between the cells are the gap itself: one pixel of the
              divider color showing through. Four bordered boxes floating on a
              fill would be four objects; this is one table. */}
          <Reveal className="mt-11 grid gap-px border border-line-soft bg-line-soft sm:grid-cols-2">
            {PLATFORM.map((item, index) => (
              <div key={item.title} className="bg-card p-6 sm:p-8">
                <p className="font-mono text-[11.5px] font-semibold tracking-[0.14em] text-rule tabular-nums">
                  {String(index + 1).padStart(2, "0")}
                </p>
                <h3 className="mt-3 font-display text-[21px] font-semibold text-ink">
                  {item.title}
                </h3>
                <p className="mt-2.5 max-w-[46ch] text-[15px] leading-relaxed text-muted">
                  {item.body}
                </p>
              </div>
            ))}
          </Reveal>
        </div>
      </section>

      {/* The compounding part: the one inverted band on the page. It is the
          spine. Without it the whole document is a single pale tone from the
          header to the footer, and nothing tells the eye where it is. */}
      <section
        id="compounding"
        aria-labelledby="compounding-heading"
        className="bg-pine-deep py-16 text-card-alt sm:py-24"
      >
        <div className="mx-auto max-w-[1160px] px-5 sm:px-7">
          <Reveal className="grid gap-7 lg:grid-cols-[0.85fr_1.15fr] lg:items-end lg:gap-[88px]">
            <div>
              <span className="mb-4 block font-mono text-xs font-semibold tracking-[0.17em] text-card-alt/65 uppercase">
                The compounding part
              </span>
              <h2
                id="compounding-heading"
                className="font-display text-[clamp(30px,3.5vw,44px)] leading-[1.12] font-medium tracking-[-0.012em]"
              >
                Every run <em className="font-medium italic">improves</em> the
                next.
              </h2>
            </div>
            <p className="max-w-[58ch] text-[17px] leading-[1.7] text-card-alt/80">
              Most automation works the same on day one and day one hundred.
              Convoy studies how each run went, tests improvements against real
              past runs, and ships them only after you approve. The routine gets
              better at your process the longer you run it.
            </p>
          </Reveal>
          {/* The loop takes the full measure. Squeezed into a column beside the
              copy, the strands are shorter than the words that ride on them and
              every caption breaks over four lines. */}
          <Reveal className="mt-14 sm:mt-16">
            <ImprovementLoop />
          </Reveal>
        </div>
      </section>

      {/* Built for scrutiny: the ledger. */}
      <section
        id="scrutiny"
        aria-labelledby="scrutiny-heading"
        className="py-16 sm:py-24"
      >
        <div className="mx-auto grid max-w-[1160px] items-start gap-9 px-5 sm:px-7 lg:grid-cols-[0.7fr_1.45fr] lg:gap-[88px]">
          <Reveal className="lg:sticky lg:top-24 lg:self-start">
            <Eyebrow>Built for scrutiny</Eyebrow>
            <SectionHeading id="scrutiny-heading">
              Answers ready before the auditor asks.
            </SectionHeading>
          </Reveal>
          <Reveal>
            <dl className="border-t border-line-soft">
              {LEDGER.map((row) => (
                <div
                  key={row.label}
                  className="grid grid-cols-1 items-baseline gap-1 border-b border-line-soft px-0.5 py-3.5 sm:grid-cols-[max-content_minmax(28px,1fr)_58%] sm:gap-4 sm:py-4"
                >
                  <dt className="font-mono text-[12.5px] font-semibold tracking-[0.1em] whitespace-nowrap text-ink uppercase">
                    {row.label}
                  </dt>
                  {/* The leader that carries the eye across. It has nothing to
                      say to a screen reader, and there is no room for it once
                      the rows stack. */}
                  <span
                    aria-hidden="true"
                    className="hidden -translate-y-1 border-b-[1.5px] border-dotted border-rule sm:block"
                  />
                  <dd className="text-[15px] leading-relaxed text-muted">
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          </Reveal>
        </div>
      </section>

      <CtaBand />
    </>
  );
}
