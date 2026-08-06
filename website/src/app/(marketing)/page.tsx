import type { Metadata } from "next";

import { CtaBand } from "./components/cta-band";
import { Hero } from "./components/hero";
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
    value: "An event history that cannot be edited; any run replayable step by step",
  },
  { label: "Files", value: "Checksummed, traceable to the step that made them, ready to export" },
  {
    label: "Access",
    value: "Per-system, per-run credentials; least privilege by default",
  },
  {
    label: "Budgets",
    value: "Dollar caps enforced ahead of the work, not counted up afterwards",
  },
  { label: "Encryption", value: "Run payloads encrypted while the work is in flight" },
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

      {/* How a run works: split B, the four phases. */}
      <section
        id="lifecycle"
        aria-labelledby="lifecycle-heading"
        className="border-t border-line-soft py-16 sm:py-24"
      >
        <div className="mx-auto grid max-w-[1160px] gap-9 px-5 sm:px-7 lg:grid-cols-[0.7fr_1.45fr] lg:gap-[88px]">
          <Reveal>
            <Eyebrow>How a run works</Eyebrow>
            <SectionHeading id="lifecycle-heading">
              Plan. Approve. Execute. <em className="font-medium italic">Learn.</em>
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

      {/* Built for scrutiny: the ledger. */}
      <section
        id="scrutiny"
        aria-labelledby="scrutiny-heading"
        className="border-t border-line-soft py-16 sm:py-24"
      >
        <div className="mx-auto grid max-w-[1160px] gap-9 px-5 sm:px-7 lg:grid-cols-[0.7fr_1.45fr] lg:gap-[88px]">
          <Reveal>
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
