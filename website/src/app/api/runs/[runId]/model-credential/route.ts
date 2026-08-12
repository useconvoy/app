/**
 * Runtime issuance of a run's model key. This endpoint is the one place a
 * customer's plaintext key leaves the website process, and only to the
 * runtime over the internal service boundary. It is machine-to-machine, not
 * session-auth: the runtime presents the same control-plane service token the
 * console uses to reach the control plane, so there is no browser and no
 * human in the loop. Nothing about the key is ever logged, and the key is
 * returned only after the token authenticates.
 *
 * A run's organization is resolved by asking the control plane itself:
 * each org's tenant is probed with GET /runs/{id} until one claims the run
 * (RLS scopes the read, so a wrong tenant is a clean 404). Dedicated
 * stacks hold one organization, so the probe is a single call.
 */
import { createHash, timingSafeEqual } from "node:crypto";
import type { NextRequest } from "next/server";

import { getRun } from "@/lib/api/runs";
import { issueCredential } from "@/lib/credentials/store";
import { isProvider } from "@/lib/credentials/verify";
import { withSystemContext } from "@/lib/db";

export const dynamic = "force-dynamic";

/** Constant-time bearer comparison over sha256 digests (equal length always). */
function tokenAuthenticates(request: NextRequest, expected: string): boolean {
  const header = request.headers.get("authorization") ?? "";
  const presented = header.startsWith("Bearer ") ? header.slice("Bearer ".length) : "";
  if (presented.length === 0) return false;
  const a = createHash("sha256").update(presented).digest();
  const b = createHash("sha256").update(expected).digest();
  return timingSafeEqual(a, b);
}

/** Every organization's (org id, runtime tenant) under a no-person context. */
async function listOrgTenants(): Promise<{ id: string; tenantId: string }[]> {
  return withSystemContext(async (client) => {
    const { rows } = await client.query<{ id: string; tenantId: string }>(
      'SELECT id, tenant_id AS "tenantId" FROM list_organizations()',
    );
    return rows;
  });
}

/** The org owning a run, found by probing each tenant's run view. */
async function orgForRun(runId: string): Promise<string | null> {
  for (const org of await listOrgTenants()) {
    const view = await getRun(
      { actorId: "system:model-credential", tenantId: org.tenantId },
      runId,
    ).catch(() => null);
    if (view) return org.id;
  }
  return null;
}

async function readProvider(request: NextRequest): Promise<string | null> {
  const queryProvider = request.nextUrl.searchParams.get("provider");
  if (queryProvider) return queryProvider;
  try {
    const body = (await request.json()) as { provider?: unknown };
    return typeof body.provider === "string" ? body.provider : null;
  } catch {
    return null;
  }
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ runId: string }> },
): Promise<Response> {
  const expected = process.env.CONVOY_CONTROL_PLANE_TOKEN;
  if (!expected) {
    // Fail closed: with no configured token there is no one we can trust.
    console.error("CONVOY_CONTROL_PLANE_TOKEN is not configured");
    return new Response(null, { status: 500 });
  }
  if (!tokenAuthenticates(request, expected)) {
    return new Response(null, { status: 401 });
  }

  const provider = await readProvider(request);
  if (!isProvider(provider)) {
    return new Response(null, { status: 400 });
  }

  const { runId } = await context.params;
  const orgId = await orgForRun(runId);
  if (!orgId) return new Response(null, { status: 404 });

  const key = await issueCredential({ orgId, provider, runId });
  if (key === null) return new Response(null, { status: 404 });

  return new Response(JSON.stringify({ key }), {
    status: 200,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
    },
  });
}
