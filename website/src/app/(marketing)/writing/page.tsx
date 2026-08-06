import type { Metadata } from "next";

import { friendlyDate } from "@/lib/format";

import { PageIntro } from "../components/page-intro";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/writing" },
  title: "Writing",
  description:
    "Notes from the Convoy team on careful software, rehearsal, and work that has to be provable.",
};

const ENTRIES = [
  {
    title: "Why we rehearse everything",
    date: new Date(2026, 6, 21),
    summary:
      "Practice is older than software. What theater, aviation, and payroll all understood long before we did, and how a rehearsal copy changes what you are willing to hand off.",
  },
  {
    title: "The checkpoint is the product",
    date: new Date(2026, 5, 9),
    summary:
      "Automation that never stops for a person is a liability wearing a party hat. How we decided where routines must hold, and why there are exactly three kinds of checkpoint.",
  },
  {
    title: "What an auditor actually wants",
    date: new Date(2026, 3, 28),
    summary:
      "We asked reviewers what makes evidence useful. The answers were humbling: names on decisions, checksums on files, and nothing that needed a meeting to explain.",
  },
] as const;

export default function WritingPage() {
  return (
    <>
      <PageIntro
        kicker="Writing"
        title="Notes on careful work"
        lede="Occasional writing from the team about routines, rehearsal, and building software that answers to auditors."
      />

      <section aria-label="All writing" className="border-t border-line">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <ul className="space-y-6">
            {ENTRIES.map((entry) => (
              <li key={entry.title}>
                <article className="rounded-lg border border-line bg-card p-8">
                  <p className="font-mono text-xs tracking-widest text-muted">
                    {friendlyDate(entry.date)}
                  </p>
                  <h2 className="mt-3 font-display text-xl font-medium text-ink">
                    {entry.title}
                  </h2>
                  <p className="mt-3 text-sm leading-relaxed text-muted">
                    {entry.summary}
                  </p>
                </article>
              </li>
            ))}
          </ul>
          <p className="mt-10 text-center text-sm text-muted">
            Full essays are on their way. Ask for early drafts when you talk
            to us.
          </p>
        </div>
      </section>
    </>
  );
}
