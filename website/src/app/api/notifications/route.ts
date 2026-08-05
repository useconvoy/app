/**
 * GET /api/notifications: the signed-in user's inbox for the bell panel.
 * Unread items plus a few recent read ones; org and user come from the
 * verified session and RLS scopes the rows to that pair.
 */
import { NextResponse } from "next/server";

import {
  NoActiveOrgError,
  requireOrgSession,
  UnauthenticatedError,
} from "@/lib/auth/session";
import { listInbox } from "@/lib/notifications/queries";

export async function GET(): Promise<NextResponse> {
  let session;
  try {
    session = await requireOrgSession();
  } catch (error) {
    if (error instanceof UnauthenticatedError || error instanceof NoActiveOrgError) {
      return NextResponse.json({ error: "not signed in" }, { status: 401 });
    }
    throw error;
  }
  const inbox = await listInbox(session.orgId, session.userId);
  return NextResponse.json(inbox);
}
