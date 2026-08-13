import type { Metadata } from "next";
import Link from "next/link";

import { PageIntro } from "../components/page-intro";

export const dynamic = "force-static";

export const metadata: Metadata = {
  alternates: { canonical: "/early-access" },
  title: "Early access",
  description: "Convoy is rolling out gradually. Sign-in is open to invited emails first.",
};

/**
 * Where a sign-in lands when the email is not on the early-access list.
 * No account was created and nothing was recorded; the copy says so and
 * points at the way in.
 */
export default function EarlyAccessPage() {
  return (
    <div className="mx-auto max-w-2xl space-y-8 px-6 py-16">
      <PageIntro
        kicker="Early access"
        title="Convoy is rolling out gradually"
        lede="Sign-in is open to invited emails first. This email is not on the list yet, so no account was created and nothing was recorded."
      />
      <div className="space-y-4 text-sm text-muted">
        <p>
          If your team is already on Convoy, ask an admin to add your email under Admin and
          then sign in again.
        </p>
        <p>
          If you are new to Convoy and want in, write to your Convoy contact and we will set
          you up.
        </p>
        <p>
          <Link href="/" className="underline">
            Back to the homepage
          </Link>
        </p>
      </div>
    </div>
  );
}
