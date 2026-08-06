import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { completeSignIn } from "@/lib/auth/sign-in";
import { localSignInAllowed, workosEnabled } from "@/lib/auth/workos";
import { isEmail, normalizeEmail } from "@/lib/orgs/validation";

export const metadata: Metadata = { title: "Sign in" };

/**
 * The unverified email + name sign-in, for a laptop or CI only. The action
 * re-checks that it is permitted, so it cannot be replayed against a
 * deployment or against one that has since gained hosted sign-in.
 */
async function devSignIn(formData: FormData) {
  "use server";
  if (!localSignInAllowed()) redirect("/sign-in");
  const email = normalizeEmail(String(formData.get("email") ?? ""));
  const name = String(formData.get("name") ?? "").trim();
  if (!isEmail(email) || name.length === 0) redirect("/sign-in?error=invalid");
  const destination = await completeSignIn({ email, name });
  redirect(destination);
}

export default async function SignInPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const { error } = await searchParams;
  const hosted = workosEnabled();
  const local = localSignInAllowed();
  return (
    <div className="rounded-md border border-line bg-card p-8">
      <h1 className="font-display text-3xl text-ink">Convoy</h1>
      <p className="mt-2 text-sm text-muted">Sign in to your organization.</p>
      {error ? (
        <div className="mt-4 rounded-sm border border-fail bg-fail-soft px-3 py-2 text-sm text-fail">
          {error === "invalid"
            ? "Enter a valid email address and your name."
            : "Sign-in did not complete. Try again."}
        </div>
      ) : null}
      {hosted ? (
        <a
          href="/api/auth/workos"
          className="mt-6 block w-full rounded-sm bg-pine px-4 py-2 text-center text-sm font-medium text-card hover:bg-pine-deep"
        >
          Continue
        </a>
      ) : !local ? (
        <div className="mt-6 rounded-sm border border-hold bg-hold-soft px-3 py-2 text-sm text-ink">
          Sign-in is not available yet. Hosted sign-in has not been set up for this site.
        </div>
      ) : (
        <form action={devSignIn} className="mt-6 space-y-4">
          <p className="border-t border-line-soft pt-4 text-xs font-medium uppercase tracking-wide text-muted">
            Local sign-in
          </p>
          <label className="block text-sm text-ink">
            Email
            <input
              type="email"
              name="email"
              required
              autoComplete="email"
              className="mt-1 w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
            />
          </label>
          <label className="block text-sm text-ink">
            Name
            <input
              type="text"
              name="name"
              required
              autoComplete="name"
              className="mt-1 w-full rounded-sm border border-line bg-card px-3 py-2 text-sm"
            />
          </label>
          <button
            type="submit"
            className="w-full rounded-sm bg-pine px-4 py-2 text-sm font-medium text-card hover:bg-pine-deep"
          >
            Sign in
          </button>
          <p className="text-xs text-muted">
            Local sign-in is for development only. It appears because hosted sign-in is not
            configured.
          </p>
        </form>
      )}
    </div>
  );
}
