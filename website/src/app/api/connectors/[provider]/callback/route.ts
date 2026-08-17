/**
 * Complete an org-level hosted install. Trust is layered: the state must
 * carry a valid signature and unexpired stamp, its nonce must match the
 * browser cookie set at start, and the signed-in session must still be
 * the org and admin that initiated. Only then does the code go to the
 * environments registry, which holds the provider app's client secret,
 * performs the exchange, and stores the credential write-only on the
 * organization's one connection for the provider. The credential never
 * passes through the website.
 *
 * Most providers return ?code=...; the GitHub App install returns
 * ?installation_id=..., which plays the same role and rides the same
 * exchange.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { exchangeOAuthConnection } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { installRedirectUri, verifyState } from "@/lib/connectors/oauth-install";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

export async function GET(
  request: Request,
  { params }: { params: Promise<{ provider: string }> },
): Promise<NextResponse> {
  const { provider } = await params;
  const safeProvider = /^[a-z0-9_]+$/.test(provider) ? provider : "connector";
  const done = (outcome: "connected" | "failed"): NextResponse => {
    const base = process.env.APP_URL ?? "http://localhost:3000";
    return NextResponse.redirect(new URL(`/app/connectors?${safeProvider}=${outcome}`, base));
  };
  if (safeProvider !== provider) return done("failed");

  const url = new URL(request.url);
  const code = url.searchParams.get("code") ?? url.searchParams.get("installation_id");
  const state = url.searchParams.get("state");
  if (!code || !state) return done("failed");

  const parsed = verifyState(state, process.env.SESSION_SECRET ?? "");
  if (!parsed) return done("failed");
  const jar = await cookies();
  const nonce = jar.get("connector_install_nonce")?.value;
  jar.delete("connector_install_nonce");
  if (!nonce || nonce !== parsed.nonce) return done("failed");

  const session = await requireOrgSession();
  if (session.orgId !== parsed.orgId || session.userId !== parsed.userId) return done("failed");
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    return done("failed");
  }

  try {
    const result = await exchangeOAuthConnection(session.orgId, {
      provider,
      code,
      redirectUri: installRedirectUri(provider),
    });
    await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
      client
        .query(
          "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
          [
            session.orgId,
            session.userId,
            "connector.connected",
            `${provider} (organization install${result.detail ? `: ${result.detail}` : ""})`,
          ],
        )
        .then(() => undefined),
    );
    return done("connected");
  } catch {
    await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
      client
        .query(
          "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
          [session.orgId, session.userId, "connector.connect_failed", `${provider} (organization install)`],
        )
        .then(() => undefined),
    );
    return done("failed");
  }
}
