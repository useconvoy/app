import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { getSession } from "@/lib/auth/session";
import { createOrganization } from "@/lib/orgs/actions";

export const metadata: Metadata = { title: "Create your organization" };

async function createOrgAction(formData: FormData) {
  "use server";
  const name = String(formData.get("name") ?? "").trim();
  if (name.length < 2) redirect("/onboarding?error=name");
  await createOrganization(name);
  redirect("/app");
}

export default async function OnboardingPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  const { error } = await searchParams;
  return (
    <div className="rounded-md border border-line bg-card p-8">
      <h1 className="font-display text-2xl text-ink">Create your organization</h1>
      <p className="mt-2 text-sm text-muted">
        You are signed in as <span className="font-mono text-xs">{session.email}</span>. Name your
        organization to get started.
      </p>
      {error === "name" ? (
        <div className="mt-4 rounded-sm border border-fail bg-fail-soft px-3 py-2 text-sm text-fail">
          Organization name must be at least 2 characters.
        </div>
      ) : null}
      <form action={createOrgAction} className="mt-6 space-y-4">
        <label className="block text-sm text-ink">
          Organization name
          <input
            type="text"
            name="name"
            required
            minLength={2}
            maxLength={120}
            className="mt-1 w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
          />
        </label>
        <button
          type="submit"
          className="w-full rounded-sm bg-pine px-4 py-2 text-sm font-medium text-card hover:bg-pine-deep"
        >
          Create organization
        </button>
      </form>
    </div>
  );
}
