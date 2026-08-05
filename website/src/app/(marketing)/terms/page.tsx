import type { Metadata } from "next";

import { PageIntro } from "../components/page-intro";

export const metadata: Metadata = {
  title: "Terms of service",
  description: "The terms that govern use of Convoy. Placeholder template.",
};

const SECTIONS = [
  {
    title: "The service",
    body: "Convoy provides software for running recurring work with human oversight. Your organization keeps ownership of its data, its routines, and everything they produce.",
  },
  {
    title: "Your account",
    body: "You are responsible for the people you invite into your organization and the roles you give them. Keep sign-in details private and tell us promptly if you believe an account is compromised.",
  },
  {
    title: "Acceptable use",
    body: "Use Convoy only for lawful work you are authorized to do, in systems you are authorized to connect. Do not attempt to reach data belonging to another organization.",
  },
  {
    title: "Fees",
    body: "Fees are set out in your order form and invoiced as agreed there. Run budgets you configure in the product are spending controls, not billing terms.",
  },
  {
    title: "Liability",
    body: "The service is provided with the care described in our security documentation. Specific warranties, limits, and remedies will be set out in the final agreement between us.",
  },
  {
    title: "Changes and termination",
    body: "We will give reasonable notice of material changes to these terms. Either party may end the agreement as it provides; on ending, you may export your records, including evidence binders.",
  },
] as const;

export default function TermsPage() {
  return (
    <>
      <PageIntro
        kicker="Legal"
        title="Terms of service"
        lede="The short version: it is your data and your judgment; we provide the software and the record."
      />

      <section aria-label="Terms" className="border-t border-line">
        <div className="mx-auto max-w-3xl px-6 py-16">
          <p className="rounded-md border border-hold bg-hold-soft px-4 py-3 text-sm text-ink">
            This is a placeholder policy. It shows the structure and tone of
            our terms while the final text is completed with counsel. It is
            not yet a binding document.
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
          <p className="mt-10 text-sm text-muted">
            Questions about these terms are welcome at hello@convoy.example.
          </p>
        </div>
      </section>
    </>
  );
}
