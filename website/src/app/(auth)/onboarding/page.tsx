import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { getSession } from "@/lib/auth/session";
import { acceptInvite, createOrganization } from "@/lib/orgs/actions";
import { listPendingInvitesForUser } from "@/lib/orgs/queries";
import { isInviteToken } from "@/lib/orgs/validation";

export const metadata: Metadata = { title: "Get started" };

const ERROR_COPY: Record<string, string> = {
  name: "Organization name must be at least 2 characters.",
  link: "That does not look like an invite link. Paste the whole link your admin sent you.",
  invalid: "That invite is not valid any more. Ask your admin for a new one.",
  expired: "That invite has expired. Ask your admin for a new one.",
  revoked: "That invite was revoked. Ask your admin for a new one.",
  accepted: "That invite was already used.",
  wrong_email: "That invite was sent to a different address. Sign in with that address to accept it.",
};

async function createOrgAction(formData: FormData) {
  "use server";
  const name = String(formData.get("name") ?? "").trim();
  if (name.length < 2) redirect("/onboarding?error=name");
  await createOrganization(name);
  redirect("/app");
}

async function joinAction(formData: FormData) {
  "use server";
  const token = String(formData.get("token") ?? "");
  const result = await acceptInvite(token);
  if (!result.ok) redirect(`/onboarding?error=${result.reason}`);
  redirect("/app");
}

/**
 * Accept a pasted link rather than a bare token: an invite token is 48 hex
 * characters, so nobody is typing one, and what a person actually has in
 * hand is the whole URL out of a message. Take either and pull the token
 * off the end.
 */
async function redeemLinkAction(formData: FormData) {
  "use server";
  const raw = String(formData.get("link") ?? "").trim();
  const token = raw.split(/[/?#]/).filter(Boolean).pop() ?? "";
  if (!isInviteToken(token)) redirect("/onboarding?error=link");
  const result = await acceptInvite(token);
  if (!result.ok) redirect(`/onboarding?error=${result.reason}`);
  redirect("/app");
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm text-ink">
      {label}
      {children}
    </label>
  );
}

const INPUT =
  "mt-1.5 w-full rounded-md border border-line bg-card px-3 py-2.5 text-sm text-ink placeholder:text-muted";

export default async function OnboardingPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  const { error } = await searchParams;
  const invites = await listPendingInvitesForUser(session.userId);

  return (
    <div className="rounded-lg border border-line bg-card p-6 sm:p-8">
      <h1 className="font-display text-2xl text-ink">
        {invites.length > 0 ? "Join your team" : "Create your organization"}
      </h1>
      <p className="mt-2 text-sm text-muted">
        You are signed in as{" "}
        <span className="font-mono text-xs break-all text-ink">{session.email}</span>.
      </p>

      {error ? (
        <div
          role="alert"
          className="mt-4 rounded-md border border-fail bg-fail-soft px-3 py-2 text-sm text-fail"
        >
          {ERROR_COPY[error] ?? ERROR_COPY.invalid}
        </div>
      ) : null}

      {/* Invitations first. Someone whose colleague already made the
          organization should not have to read past a form that makes them a
          second one. */}
      {invites.length > 0 ? (
        <section aria-label="Your invitations" className="mt-6 space-y-2.5">
          {invites.map((invite) => (
            <form
              key={invite.token}
              action={joinAction}
              className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-line bg-field px-4 py-3.5"
            >
              <input type="hidden" name="token" value={invite.token} />
              <span className="min-w-0">
                <span className="block text-sm font-medium text-ink">{invite.orgName}</span>
                <span className="mt-0.5 block font-mono text-[11px] tracking-[0.08em] text-muted uppercase">
                  invited as {invite.role}
                </span>
              </span>
              <button
                type="submit"
                className="shrink-0 rounded-md bg-pine px-4 py-2 text-sm font-medium text-card-alt hover:bg-pine-deep"
              >
                Join
              </button>
            </form>
          ))}
        </section>
      ) : null}

      <details className="group mt-6" open={invites.length === 0}>
        <summary className="cursor-pointer list-none text-sm font-medium text-ink marker:content-['']">
          <span className="group-open:hidden">Create a new organization instead</span>
          <span className="hidden group-open:inline">Create a new organization</span>
        </summary>
        <form action={createOrgAction} className="mt-4 space-y-4">
          <Field label="Organization name">
            <input
              type="text"
              name="name"
              required
              minLength={2}
              maxLength={120}
              autoComplete="organization"
              className={INPUT}
            />
          </Field>
          <button
            type="submit"
            className="w-full rounded-md bg-pine px-4 py-2.5 text-sm font-medium text-card-alt hover:bg-pine-deep"
          >
            Create organization
          </button>
        </form>
      </details>

      {/* The fallback for the gap this page exists to close: an invite that
          was sent before the address on it was the one they signed in with,
          or one to an address this account does not carry. */}
      <details className="mt-4 border-t border-line-soft pt-4">
        <summary className="cursor-pointer list-none text-sm text-muted marker:content-['']">
          I have an invite link
        </summary>
        <form action={redeemLinkAction} className="mt-3 space-y-3">
          <Field label="Invite link">
            <input
              type="text"
              name="link"
              required
              inputMode="url"
              placeholder="https://deployconvoy.com/invite/…"
              className={INPUT}
            />
          </Field>
          <button
            type="submit"
            className="w-full rounded-md border border-line px-4 py-2.5 text-sm font-medium text-ink hover:border-muted"
          >
            Use invite link
          </button>
        </form>
      </details>
    </div>
  );
}
