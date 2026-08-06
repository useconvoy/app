import type { Metadata } from "next";

import { PageIntro } from "../components/page-intro";
import { DemoRequestForm } from "./demo-form";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/demo" },
  title: "Request a demo",
  description:
    "Tell us about the routine work you want to hand off and we will walk you through a live rehearsal.",
};

const WHAT_TO_EXPECT = [
  "A thirty-minute walkthrough of a real routine running in rehearsal.",
  "The checkpoint moment: watching a run stop and wait for a person.",
  "The outbox review: reading exactly what a routine would have sent.",
  "Straight answers on security, roles, and what the record looks like.",
] as const;

export default function DemoPage() {
  return (
    <>
      <PageIntro
        kicker="Request a demo"
        title="See a routine run carefully"
        lede="Tell us a little about your team and the recurring work you would like to hand off. A person reads every request and replies within two working days."
      />

      <section aria-label="Demo request" className="border-t border-line">
        <div className="mx-auto grid max-w-5xl items-start gap-12 px-6 py-16 lg:grid-cols-[2fr_3fr]">
          <aside className="rounded-lg border border-line bg-card p-6">
            <h2 className="text-sm font-semibold text-ink">What to expect</h2>
            <ul className="mt-4 list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted">
              {WHAT_TO_EXPECT.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </aside>
          <DemoRequestForm />
        </div>
      </section>
    </>
  );
}
