import type { Metadata } from "next";

import { CtaBand } from "../components/cta-band";
import { PageIntro } from "../components/page-intro";

export const metadata: Metadata = {
  title: "Security",
  description:
    "How Convoy handles isolation, roles, audit trails, credentials, and evidence. Honest and concrete, no badge theater.",
};

const ROLES = [
  {
    name: "Admin",
    body: "Manages people, policies, budgets, and billing for the organization.",
  },
  {
    name: "Operator",
    body: "Runs and promotes routines, manages workspaces and systems.",
  },
  {
    name: "Member",
    body: "Works with assigned routines, answers checkpoints, gives feedback.",
  },
  {
    name: "Viewer",
    body: "Read only. Built for external reviewers and audit firms who need to see the record without being able to touch anything.",
  },
] as const;

function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section id={id} aria-labelledby={`${id}-heading`} className="border-t border-line">
      <div className="mx-auto grid max-w-6xl gap-8 px-6 py-14 lg:grid-cols-[1fr_2fr]">
        <h2
          id={`${id}-heading`}
          className="font-display text-2xl font-medium text-ink"
        >
          {title}
        </h2>
        <div className="max-w-2xl space-y-4 leading-relaxed text-muted">
          {children}
        </div>
      </div>
    </section>
  );
}

export default function SecurityPage() {
  return (
    <>
      <PageIntro
        kicker="Security and trust"
        title="Built for audit from the first day"
        lede="Convoy exists for work that has to be right and has to be provable. Here is concretely how we treat your organization, your people, and your records."
      />

      <Section id="isolation" title="Your organization stands alone">
        <p>
          Every record in Convoy belongs to exactly one organization, and that
          boundary is enforced in the database on every query, not just in the
          interface. There is no path where a request from one organization
          reads another organization&rsquo;s data.
        </p>
        <p>
          When Convoy staff help operate a routine inside your organization,
          they act under a named account with a defined role, and everything
          they do lands in your audit trail exactly as your own team&rsquo;s
          actions do.
        </p>
      </Section>

      <Section id="roles" title="Four roles, checked on the server">
        <p>
          Access follows a small set of roles, and every action is checked on
          the server, never just hidden in the interface:
        </p>
        <ul className="space-y-3">
          {ROLES.map((role) => (
            <li
              key={role.name}
              className="rounded-lg border border-line bg-card p-4"
            >
              <p className="font-semibold text-ink">{role.name}</p>
              <p className="mt-1 text-sm">{role.body}</p>
            </li>
          ))}
        </ul>
      </Section>

      <Section id="audit" title="An audit trail with names in it">
        <p>
          Every step a routine takes, every plan approved, every checkpoint
          answered, and every administrative change is recorded with who did
          it and when. Approvals are individual acts by named people; there is
          no anonymous yes.
        </p>
        <p>
          When a checkpoint is assigned to a team, the person who answers is
          the person on the record. Attribution survives all the way into the
          evidence you export.
        </p>
      </Section>

      <Section id="credentials" title="This website never holds your keys">
        <p>
          The credentials that connect Convoy to your systems are never stored
          in the website or its database. The website holds only references;
          the secrets live in a separate, locked-down layer, and they never
          appear in logs, exports, or anywhere a person could casually read
          them.
        </p>
      </Section>

      <Section id="evidence" title="Evidence binders, not screenshots">
        <p>
          Any run can be exported as an evidence binder: the files it
          produced, a manifest of what happened, and checksums so the
          contents can be verified later. It is a single archive you can hand
          to an auditor without editing a thing.
        </p>
      </Section>

      <Section id="certifications" title="A straight answer on certifications">
        <p>
          We are built for audit, and we hold ourselves to the practices
          above, but we will not decorate this page with badges we have not
          earned. If a formal certification matters to your review, ask us
          directly and we will tell you exactly where we stand and what our
          roadmap is.
        </p>
      </Section>

      <CtaBand
        title="Put your hardest questions to us"
        body="Bring your security review. We would rather answer it early than impress you late."
      />
    </>
  );
}
