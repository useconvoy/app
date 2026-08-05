import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { getSession } from "@/lib/auth/session";
import { switchOrganization } from "@/lib/orgs/actions";
import { listUserOrgs } from "@/lib/orgs/queries";

export const metadata: Metadata = { title: "Choose an organization" };

async function pickOrgAction(formData: FormData) {
  "use server";
  const orgId = String(formData.get("orgId") ?? "");
  // switchOrganization verifies the membership server-side before the
  // session changes; the hidden input is only a selection, never trust.
  await switchOrganization(orgId);
  redirect("/app");
}

export default async function SwitchPage() {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  const orgs = await listUserOrgs(session.userId);
  if (orgs.length === 0) redirect("/onboarding");
  return (
    <div className="rounded-md border border-line bg-card p-8">
      <h1 className="font-display text-2xl text-ink">Choose an organization</h1>
      <p className="mt-2 text-sm text-muted">You belong to more than one. Pick where to work.</p>
      <ul className="mt-6 space-y-2">
        {orgs.map((org) => (
          <li key={org.id}>
            <form action={pickOrgAction}>
              <input type="hidden" name="orgId" value={org.id} />
              <button
                type="submit"
                className="flex w-full items-center justify-between rounded-sm border border-line bg-card px-4 py-3 text-left text-sm text-ink hover:border-pine"
              >
                <span>{org.name}</span>
                <span className="font-mono text-xs uppercase text-muted">{org.role}</span>
              </button>
            </form>
          </li>
        ))}
      </ul>
      <p className="mt-6 text-center text-xs text-muted">
        <Link href="/onboarding" className="underline">
          Create a new organization
        </Link>
      </p>
    </div>
  );
}
