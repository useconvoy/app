import type { Metadata } from "next";

import { friendlyDate } from "@/lib/format";

import { PageIntro } from "../components/page-intro";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/changelog" },
  title: "Changelog",
  description:
    "What has shipped in Convoy recently, in plain language.",
};

const ENTRIES = [
  {
    title: "Checkpoint deadlines you can see coming",
    date: new Date(2026, 6, 30),
    items: [
      "Held items now show a countdown and say plainly what happens if the deadline passes.",
      "Reviewers get a nudge before a checkpoint times out, not after.",
      "Team-held checkpoints show who answered once someone does.",
    ],
  },
  {
    title: "Evidence binders from any run",
    date: new Date(2026, 6, 9),
    items: [
      "Export a run as a single archive: files, a manifest of what happened, and checksums for verification.",
      "Viewers can export too, so external reviewers pull their own evidence.",
    ],
  },
  {
    title: "A clock for rehearsals",
    date: new Date(2026, 5, 18),
    items: [
      "Fast-forward a rehearsal through days of waiting in minutes and watch the whole story unfold.",
      "Rehearsal timestamps now show rehearsal time first, real time on hover.",
      "The outbox review reads more clearly: every message it would have sent, word for word.",
    ],
  },
] as const;

export default function ChangelogPage() {
  return (
    <>
      <PageIntro
        kicker="Changelog"
        title="What has shipped"
        lede="Product changes in plain language, most recent first. If a change affects how your routines behave, it is written here."
      />

      <section aria-label="Release history" className="border-t border-line">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <ol className="space-y-6">
            {ENTRIES.map((entry) => (
              <li key={entry.title}>
                <article className="rounded-lg border border-line bg-card p-8">
                  <p className="font-mono text-xs tracking-widest text-muted">
                    {friendlyDate(entry.date)}
                  </p>
                  <h2 className="mt-3 font-display text-xl font-medium text-ink">
                    {entry.title}
                  </h2>
                  <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
                    {entry.items.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </article>
              </li>
            ))}
          </ol>
        </div>
      </section>
    </>
  );
}
