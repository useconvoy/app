import type { Metadata } from "next";
import Link from "next/link";

import { CtaBand } from "../components/cta-band";
import { PageIntro } from "../components/page-intro";

export const metadata: Metadata = {
  title: "Company",
  description:
    "Why Convoy exists, the principles we build by, and how to reach us.",
};

const PRINCIPLES = [
  {
    title: "Judgment stays human",
    body: "Software can carry the routine; it should never quietly absorb the decisions. Every Convoy routine is built around the moments where a person must say yes.",
  },
  {
    title: "Rehearse before you act",
    body: "Nothing earns the right to touch real systems without showing its work first. Practice is not a feature we added; it is the order of operations.",
  },
  {
    title: "Records over reassurance",
    body: "Trust us less, verify us more. Everything Convoy does is written down, attributed, and exportable, so the proof does not depend on our word.",
  },
  {
    title: "Plain language or nothing",
    body: "If a step cannot be described in a sentence a reviewer would sign, it does not belong in a routine. We write for the person accountable for the outcome.",
  },
  {
    title: "Spend within lines",
    body: "Every run has a cap agreed in advance. Careful work includes careful spending, and a surprise invoice is a kind of failure.",
  },
] as const;

export default function CompanyPage() {
  return (
    <>
      <PageIntro
        kicker="Company"
        title="Careful work deserves careful software"
        lede="Convoy is built for the recurring work organizations must get right: reviews, checks, and chases that carry real consequences when they slip."
      />

      <section aria-labelledby="mission-heading" className="border-t border-line">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <h2
            id="mission-heading"
            className="font-display text-2xl font-medium text-ink"
          >
            Our mission
          </h2>
          <div className="mt-5 space-y-4 leading-relaxed text-muted">
            <p>
              A great deal of important work is routine: the quarterly access
              review, the vendor file refresh, the policy attestation chase.
              It is work that must be done exactly, on time, every time, and
              it usually lands on people who have more valuable judgment to
              give than the grind allows.
            </p>
            <p>
              We build Convoy so that the grind moves to software while the
              judgment stays with people, and so that every step of both is
              recorded well enough to hand to an auditor without apology. Our
              measure of success is simple: the work gets done more carefully
              than before, and everyone involved can prove it.
            </p>
          </div>
        </div>
      </section>

      <section
        aria-labelledby="principles-heading"
        className="border-t border-line bg-card"
      >
        <div className="mx-auto max-w-5xl px-6 py-16">
          <h2
            id="principles-heading"
            className="font-display text-2xl font-medium text-ink"
          >
            Principles we build by
          </h2>
          <ol className="mt-8 grid gap-6 md:grid-cols-2">
            {PRINCIPLES.map((principle, index) => (
              <li
                key={principle.title}
                className="rounded-lg border border-line bg-field p-6"
              >
                <p className="font-mono text-xs tracking-widest text-muted">
                  {String(index + 1).padStart(2, "0")}
                </p>
                <h3 className="mt-3 font-semibold text-ink">
                  {principle.title}
                </h3>
                <p className="mt-2 text-sm leading-relaxed text-muted">
                  {principle.body}
                </p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section aria-labelledby="contact-heading" className="border-t border-line">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <h2
            id="contact-heading"
            className="font-display text-2xl font-medium text-ink"
          >
            Contact
          </h2>
          <div className="mt-5 space-y-4 leading-relaxed text-muted">
            <p>
              The fastest way to talk with us is to{" "}
              <Link href="/demo" className="text-pine underline hover:text-pine-deep">
                request a demo
              </Link>
              . We read every submission ourselves and reply within two
              working days.
            </p>
            <p>
              For everything else, write to{" "}
              <a
                href="mailto:hello@convoy.example"
                className="text-pine underline hover:text-pine-deep"
              >
                hello@convoy.example
              </a>
              . Security questions are welcome at the same address and get
              priority.
            </p>
          </div>
        </div>
      </section>

      <CtaBand />
    </>
  );
}
