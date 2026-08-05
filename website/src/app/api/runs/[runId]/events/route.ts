/**
 * SSE proxy for a run's event stream (CLAUDE.md rule 11). The BFF holds the
 * bearer and translates the verified session into actor/tenant headers; the
 * browser never talks to the control plane. Frames pipe through unchanged
 * (id:/event:/data:), so the browser's native EventSource resume works: on
 * reconnect it re-sends Last-Event-ID, which rides through to the runtime.
 */
import type { NextRequest } from "next/server";

import { controlPlaneUrl, sseHeaders } from "@/lib/api/client";
import {
  NoActiveOrgError,
  requireOrgSession,
  UnauthenticatedError,
} from "@/lib/auth/session";
import { orgTenantId } from "@/lib/routines/queries";

export const dynamic = "force-dynamic";

const STREAM_HEADERS = {
  "Content-Type": "text/event-stream",
  "Cache-Control": "no-cache, no-transform",
  Connection: "keep-alive",
  // Defensive: proxies must not buffer a live stream.
  "X-Accel-Buffering": "no",
} as const;

export async function GET(
  request: NextRequest,
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

  const { runId } = await context.params;
  let tenantId: string;
  try {
    tenantId = await orgTenantId(session.orgId);
  } catch {
    // A session pointing at a vanished org resolves no tenant: not found.
    return new Response(null, { status: 404 });
  }

  const upstreamUrl = new URL(
    `/runs/${encodeURIComponent(runId)}/events`,
    controlPlaneUrl(),
  );
  const after = request.nextUrl.searchParams.get("after");
  if (after !== null) upstreamUrl.searchParams.set("after", after);
  const lastEventId = request.headers.get("last-event-id") ?? undefined;

  let upstream: Response;
  try {
    upstream = await fetch(upstreamUrl, {
      headers: {
        ...sseHeaders({ actorId: session.email, tenantId }, lastEventId),
        Accept: "text/event-stream",
      },
      cache: "no-store",
      // Client gone -> upstream connection released immediately.
      signal: request.signal,
    });
  } catch {
    // Transient upstream failure: close cleanly with an empty 200 stream so
    // the browser's EventSource reconnects on its own schedule.
    return new Response("", { status: 200, headers: STREAM_HEADERS });
  }

  // Wrong tenant or unknown run is simply not found; never probed further.
  if (upstream.status === 404) return new Response(null, { status: 404 });
  if (!upstream.ok || !upstream.body) {
    return new Response("", { status: 200, headers: STREAM_HEADERS });
  }

  // Pipe the byte stream through untouched: no buffering, no reframing.
  return new Response(upstream.body, { status: 200, headers: STREAM_HEADERS });
}
