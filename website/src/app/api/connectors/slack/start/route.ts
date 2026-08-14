/**
 * Start the org-level Slack install: admin-gated, mints the signed state
 * and its browser nonce cookie, then hands the browser to Slack's consent
 * screen. The whole organization gets the resulting connection; there is
 * nothing per-user to keep.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { requireOrgSession } from "@/lib/auth/session";
import {
  newNonce,
  signState,
  slackAuthorizeUrl,
  slackOAuthConfigured,
  slackRedirectUri,
} from "@/lib/connectors/slack-oauth";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

export async function GET(): Promise<NextResponse> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    return NextResponse.redirect(new URL("/app/connectors", process.env.APP_URL ?? "http://localhost:3000"));
  }
  if (!slackOAuthConfigured()) {
    return NextResponse.redirect(new URL("/app/connectors", process.env.APP_URL ?? "http://localhost:3000"));
  }

  const nonce = newNonce();
  const state = signState(
    { orgId: session.orgId, userId: session.userId, nonce },
    process.env.SESSION_SECRET ?? "",
  );
  const jar = await cookies();
  jar.set("slack_oauth_nonce", nonce, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: 600,
    path: "/api/connectors/slack",
  });
  return NextResponse.redirect(
    slackAuthorizeUrl({
      clientId: process.env.SLACK_CLIENT_ID ?? "",
      redirectUri: slackRedirectUri(),
      state,
    }),
  );
}
