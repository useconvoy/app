/**
 * POST /api/notifications/read: mark the caller's own notifications read.
 * Ids arrive from the client but the update is bound to the session's
 * (org, user) pair in SQL and by RLS, so nobody can flip another inbox.
 */
import { NextResponse } from "next/server";
import { z } from "zod";

import {
  NoActiveOrgError,
  requireOrgSession,
  UnauthenticatedError,
} from "@/lib/auth/session";
import { markNotificationsRead } from "@/lib/notifications/queries";

const bodySchema = z.object({
  ids: z.array(z.uuid()).min(1).max(100),
});

export async function POST(request: Request): Promise<NextResponse> {
  let session;
  try {
    session = await requireOrgSession();
  } catch (error) {
    if (error instanceof UnauthenticatedError || error instanceof NoActiveOrgError) {
      return NextResponse.json({ error: "not signed in" }, { status: 401 });
    }
    throw error;
  }
  const parsed = bodySchema.safeParse(await request.json().catch(() => null));
  if (!parsed.success) {
    return NextResponse.json({ error: "expected ids: string[]" }, { status: 400 });
  }
  const updated = await markNotificationsRead(session.orgId, session.userId, parsed.data.ids);
  return NextResponse.json({ updated });
}
