import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { getSession } from "@/lib/auth/session";
import { acceptInvite } from "@/lib/orgs/actions";
import { getInviteForToken } from "@/lib/orgs/queries";
import { inviteVerdict, isInviteToken, normalizeEmail } from "@/lib/orgs/validation";

export const metadata: Metadata = { title: "Invitation" };

const PROBLEM_COPY: Record<string, string> = {
  invalid: "This invite link is not valid. Ask your admin for a new one.",
  expired: "This invite has expired. Ask your admin for a new one.",
  revoked: "This invite was revoked. Ask your admin for a new one.",
  accepted: "This invite was already used.",
  wrong_email: "This invite was sent to a different email address. Sign in with that address to accept it.",
};

function ProblemBlock({ reason }: { reason: string }) {
  return (
    <div className="rounded-md border border-line bg-card p-8">
      <h1 className="font-display text-2xl text-ink">Invitation</h1>
      <div className="mt-4 rounded-sm border border-fail bg-fail-soft px-3 py-2 text-sm text-fail">
        {PROBLEM_COPY[reason] ?? PROBLEM_COPY.invalid}
      </div>
    </div>
  );
}

async function acceptAction(formData: FormData) {
  "use server";
  const token = String(formData.get("token") ?? "");
  const result = await acceptInvite(token);
  if (!result.ok) redirect(`/invite/${token}`);
  // acceptInvite already switched the session to the joined org.
  redirect("/app");
}

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const session = await getSession();
  if (!session) redirect("/sign-in");
  if (!isInviteToken(token)) return <ProblemBlock reason="invalid" />;
  const invite = await getInviteForToken(token, session.userId);
  if (!invite) return <ProblemBlock reason="invalid" />;
  const verdict = inviteVerdict(invite);
  if (verdict !== "ok") return <ProblemBlock reason={verdict} />;
  if (normalizeEmail(invite.email) !== normalizeEmail(session.email)) {
    return <ProblemBlock reason="wrong_email" />;
  }
  return (
    <div className="rounded-md border border-line bg-card p-8">
      <h1 className="font-display text-2xl text-ink">Join {invite.orgName}</h1>
      <p className="mt-2 text-sm text-muted">
        You are invited to join <span className="text-ink">{invite.orgName}</span> as{" "}
        <span className="font-mono text-xs uppercase">{invite.role}</span>.
      </p>
      <form action={acceptAction} className="mt-6">
        <input type="hidden" name="token" value={token} />
        <button
          type="submit"
          className="w-full rounded-sm bg-pine px-4 py-2 text-sm font-medium text-card hover:bg-pine-deep"
        >
          Accept invitation
        </button>
      </form>
    </div>
  );
}
