/**
 * Complete the org-level Slack install. Trust is layered: the state must
 * carry a valid signature and unexpired stamp, its nonce must match the
 * browser cookie set at start, and the signed-in session must still be
 * the org and admin that initiated. Only then does the code go to the
 * environments registry, which holds the app's client secret, performs
 * the exchange, and stores the workspace bot token write-only on the
 * organization's one Slack connection. The credential never passes
 * through the website.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { exchangeOAuthConnection } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { installRedirectUri, verifyState } from "@/lib/connectors/oauth-install";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

function done(outcome: "connected" | "failed"): NextResponse {
  const base = process.env.APP_URL ?? "http://localhost:3000";
  return NextResponse.redirect(new URL(`/app/connectors?slack=${outcome}`, base));
}

export async function GET(request: Request): Promise<NextResponse> {
  const url = new URL(request.url);
  const code = url.searchParams.get("code");
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
      provider: "slack",
      code,
      redirectUri: installRedirectUri("slack"),
    });
    await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
      client
        .query(
          "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
          [
            session.orgId,
            session.userId,
            "connector.connected",
            `messaging (workspace install${result.detail ? `: ${result.detail}` : ""})`,
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
          [session.orgId, session.userId, "connector.connect_failed", "messaging (workspace install)"],
        )
        .then(() => undefined),
    );
    return done("failed");
  }
}
