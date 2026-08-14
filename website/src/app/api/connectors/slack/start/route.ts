/**
 * Start the org-level Slack install: admin-gated, mints the signed state
 * and its browser nonce cookie, then hands the browser to the consent
 * screen the registry's provider directory describes. The website adds no
 * provider knowledge of its own; when the registry offers no hosted
 * install for Slack, this quietly returns to the Connectors page.
 */
import { cookies } from "next/headers";
import { NextResponse } from "next/server";

import { listProviderDirectory } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import {
  authorizeUrl,
  installRedirectUri,
  newNonce,
  signState,
} from "@/lib/connectors/oauth-install";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

function back(): NextResponse {
  return NextResponse.redirect(
    new URL("/app/connectors", process.env.APP_URL ?? "http://localhost:3000"),
  );
}

export async function GET(): Promise<NextResponse> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    return back();
  }
  const directory = await listProviderDirectory(session.orgId).catch(() => null);
  const oauth = directory?.find((entry) => entry.provider === "slack")?.oauth;
  if (!oauth) return back();

  const nonce = newNonce();
  const state = signState(
    { orgId: session.orgId, userId: session.userId, nonce },
    process.env.SESSION_SECRET ?? "",
  );
  const jar = await cookies();
  jar.set("connector_install_nonce", nonce, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: 600,
    path: "/api/connectors",
  });
  return NextResponse.redirect(
    authorizeUrl(oauth, { redirectUri: installRedirectUri("slack"), state }),
  );
}
