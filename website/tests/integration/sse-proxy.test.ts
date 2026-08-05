// @vitest-environment node
/**
 * The SSE proxy route against the real local control plane: session
 * enforcement, tenant scoping (a wrong-tenant run id is a 404, never
 * probed), pass-through streaming, and Last-Event-ID resume. The session
 * and tenant lookup are mocked at the module seam; everything downstream
 * of the route handler is real. Skips cleanly when the control plane is
 * not running.
 */
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import type { Session } from "@/lib/auth/session";

vi.mock("@/lib/auth/session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/auth/session")>();
  return { ...actual, requireOrgSession: vi.fn() };
});

// The tenant lookup needs Postgres; the proxy only needs its value.
vi.mock("@/lib/routines/queries", () => ({
  orgTenantId: vi.fn(),
}));

import { NextRequest } from "next/server";

import { GET } from "@/app/api/runs/[runId]/events/route";
import { requireOrgSession, UnauthenticatedError } from "@/lib/auth/session";
import { orgTenantId } from "@/lib/routines/queries";

const BASE = process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700";
const TOKEN = process.env.CONVOY_CONTROL_PLANE_TOKEN ?? "e2e-dev-token";
const TENANT = "tenant-sse-proxy-test";

const controlPlaneUp = await fetch(`${BASE}/openapi.json`, { signal: AbortSignal.timeout(2000) })
  .then((response) => response.ok)
  .catch(() => false);

function sessionFor(orgId: string): Session & { orgId: string } {
  return {
    userId: "user-sse-proxy",
    email: "j.doe@example.com",
    name: "J. Doe",
    orgId,
  };
}

function proxyRequest(runId: string, init?: { after?: string; lastEventId?: string }) {
  const url = new URL(`http://localhost:3100/api/runs/${runId}/events`);
  if (init?.after) url.searchParams.set("after", init.after);
  const request = new NextRequest(url, {
    headers: init?.lastEventId ? { "Last-Event-ID": init.lastEventId } : {},
  });
  return GET(request, { params: Promise.resolve({ runId }) });
}

/** Read SSE frames from the proxied stream until a terminal event. */
async function readFrames(response: Response, timeoutMs = 30_000): Promise<string> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let text = "";
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    text += decoder.decode(value, { stream: true });
    if (text.includes("run_completed") || text.includes("run_failed")) break;
  }
  await reader.cancel().catch(() => undefined);
  return text;
}

describe.skipIf(!controlPlaneUp)("SSE proxy route (real control plane)", () => {
  let runId: string;

  beforeAll(async () => {
    process.env.CONVOY_CONTROL_PLANE_TOKEN = TOKEN;
    process.env.CONVOY_CONTROL_PLANE_URL = BASE;
    const response = await fetch(`${BASE}/runs`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        "X-Actor-Id": "sse-proxy@convoy.test",
        "X-Tenant-Id": TENANT,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ goal: "Proxy integration run", environment_id: "stub-local", budget_usd: "5" }),
    });
    expect(response.status).toBeLessThan(300);
    runId = ((await response.json()) as { run_id: string }).run_id;
    // Let the stub run land so streams replay a complete sequence.
    await new Promise((resolve) => setTimeout(resolve, 4000));
  }, 20_000);

  afterAll(() => {
    vi.restoreAllMocks();
  });

  it("rejects unauthenticated requests with 401", async () => {
    vi.mocked(requireOrgSession).mockRejectedValueOnce(new UnauthenticatedError());
    const response = await proxyRequest(runId);
    expect(response.status).toBe(401);
  });

  it("returns 404 for a wrong-tenant run id without probing further", async () => {
    vi.mocked(requireOrgSession).mockResolvedValueOnce(sessionFor("org-other"));
    vi.mocked(orgTenantId).mockResolvedValueOnce("tenant-sse-proxy-other");
    const response = await proxyRequest(runId);
    expect(response.status).toBe(404);
  });

  it("returns 404 for an unknown run id", async () => {
    vi.mocked(requireOrgSession).mockResolvedValueOnce(sessionFor("org-mine"));
    vi.mocked(orgTenantId).mockResolvedValueOnce(TENANT);
    const response = await proxyRequest("run-does-not-exist");
    expect(response.status).toBe(404);
  });

  it("pipes the byte stream through unchanged for the right tenant", async () => {
    vi.mocked(requireOrgSession).mockResolvedValueOnce(sessionFor("org-mine"));
    vi.mocked(orgTenantId).mockResolvedValueOnce(TENANT);
    const response = await proxyRequest(runId);
    expect(response.status).toBe(200);
    expect(response.headers.get("content-type")).toContain("text/event-stream");
    const text = await readFrames(response);
    expect(text).toContain("id: 1");
    expect(text).toContain("event: run_started");
    expect(text).toContain('"seq": 1');
    expect(text).toContain("event: run_completed");
  });

  it("resumes from Last-Event-ID instead of replaying the stream", async () => {
    vi.mocked(requireOrgSession).mockResolvedValueOnce(sessionFor("org-mine"));
    vi.mocked(orgTenantId).mockResolvedValueOnce(TENANT);
    const response = await proxyRequest(runId, { lastEventId: "2" });
    const text = await readFrames(response);
    expect(text).not.toContain("event: run_started");
    expect(text).toContain("event: step_started");
    expect(text).toContain("event: run_completed");
  });

  it("honors the after query parameter the same way", async () => {
    vi.mocked(requireOrgSession).mockResolvedValueOnce(sessionFor("org-mine"));
    vi.mocked(orgTenantId).mockResolvedValueOnce(TENANT);
    const response = await proxyRequest(runId, { after: "2" });
    const text = await readFrames(response);
    expect(text).not.toContain("event: run_started");
    expect(text).toContain("event: run_completed");
  });
});
