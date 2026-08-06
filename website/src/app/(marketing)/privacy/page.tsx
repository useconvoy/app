import type { Metadata } from "next";

import { PageIntro } from "../components/page-intro";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/privacy" },
  title: "Privacy policy",
  description:
    "What Convoy collects, why, and what we never do with it. Placeholder template.",
};

const SECTIONS = [
  {
    title: "What we collect",
    body: "Account details for the people in your organization (name, work email, role), the routines and records your organization creates in the product, and the operational logs needed to run the service reliably.",
  },
  {
    title: "What we use it for",
    body: "To provide the service: running routines, holding checkpoints for the right people, keeping the audit trail, and supporting you. We do not sell personal information or use your records for advertising.",
  },
  {
    title: "What we never store here",
    body: "Credentials for the systems your routines connect to are never stored in this website or its database. They live in a separate secrets layer and never appear in logs or exports.",
  },
  {
    title: "Who can see your data",
    body: "People in your organization, according to the roles you set. Convoy staff who help operate routines act under named accounts, and their actions appear in your audit trail.",
  },
  {
    title: "Retention and export",
    body: "Your organization controls its records and can export them, including evidence binders, at any time. When an agreement ends, we delete organization data on the schedule set out in the final policy.",
  },
  {
    title: "Contact",
    body: "Privacy questions and requests go to hello@convoy.example and are answered by a person.",
  },
] as const;

export default function PrivacyPage() {
  return (
    <>
      <PageIntro
        kicker="Legal"
        title="Privacy policy"
        lede="The short version: we collect what the service needs, nothing is sold, and your credentials never live here."
      />

      <section aria-label="Privacy policy" className="border-t border-line">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <p className="rounded-md border border-hold bg-hold-soft px-4 py-3 text-sm text-ink">
            This is a placeholder policy. It shows the structure and tone of
            our privacy commitments while the final text is completed with
            counsel. It is not yet a binding document.
          </p>
          <dl className="mt-10 space-y-8">
            {SECTIONS.map((section) => (
              <div key={section.title}>
                <dt className="font-semibold text-ink">{section.title}</dt>
                <dd className="mt-2 leading-relaxed text-muted">
                  {section.body}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      </section>
    </>
  );
}
