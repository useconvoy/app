/**
 * Complete the org-level Slack install. Trust is layered: the state must
 * carry a valid signature and unexpired stamp, its nonce must match the
 * browser cookie set at start, the signed-in session must still be the
 * org and admin that initiated, and only then is the code exchanged and
 * the workspace bot token attached to the organization's one Slack
 * connection (write-only in the registry, same as a pasted token).
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { connectManagedSystem } from "@/lib/api/environments";
import { declaredManifest } from "@/lib/api/environments-mapping";
import { requireOrgSession } from "@/lib/auth/session";
import {
  exchangeSlackCode,
  slackRedirectUri,
  verifyState,
} from "@/lib/connectors/slack-oauth";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { catalogSystem } from "@/lib/workspaces/system-catalog";

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
  const nonce = jar.get("slack_oauth_nonce")?.value;
  jar.delete("slack_oauth_nonce");
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

  const system = catalogSystem("messaging");
  if (!system?.connection) return done("failed");
  try {
    const exchange = await exchangeSlackCode({ code, redirectUri: slackRedirectUri() });
    await connectManagedSystem(session.orgId, {
      provider: system.connection.provider,
      displayName: system.displayName,
      manifest: declaredManifest(system.connection),
      secretValue: exchange.botToken,
    });
    await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
      client
        .query(
          "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
          [
            session.orgId,
            session.userId,
            "connector.connected",
            `messaging (workspace install${exchange.teamName ? `: ${exchange.teamName}` : ""})`,
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
