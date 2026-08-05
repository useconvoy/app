/**
 * Evidence binder export (C10): GET streams the zip. Session-verified,
 * export_evidence-gated server-side (every role holds it in v1; the org
 * policy toggle for viewers arrives with W6 policies). A 404 may mean
 * wrong tenant and stays a 404, never probed further.
 */
import type { NextRequest } from "next/server";

import {
  NoActiveOrgError,
  requireOrgSession,
  UnauthenticatedError,
} from "@/lib/auth/session";
import { assembleBinder } from "@/lib/evidence/binder";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { orgTenantId } from "@/lib/routines/queries";

export const dynamic = "force-dynamic";

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ runId: string }> },
): Promise<Response> {
  let session;
  try {
    session = await requireOrgSession();
  } catch (error) {
    if (error instanceof UnauthenticatedError || error instanceof NoActiveOrgError) {
      return new Response(null, { status: 401 });
    }
    throw error;
  }

  const membership = await getMembership(session.orgId, session.userId);
  if (!membership || membership.status !== "active") {
    return new Response(null, { status: 403 });
  }
  if (!can("export_evidence", membership.role, membership.capabilities)) {
    return new Response(null, { status: 403 });
  }

  const { runId } = await context.params;
  let tenantId: string;
  try {
    tenantId = await orgTenantId(session.orgId);
  } catch {
    return new Response(null, { status: 404 });
  }

  const zip = await assembleBinder({ actorId: session.email, tenantId }, runId, session.email);
  if (!zip) return new Response(null, { status: 404 });

  return new Response(new Uint8Array(zip), {
    status: 200,
    headers: {
      "Content-Type": "application/zip",
      "Content-Disposition": `attachment; filename="evidence-binder-${runId}.zip"`,
      "Cache-Control": "no-store",
    },
  });
}
