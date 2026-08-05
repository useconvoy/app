/**
 * The single authenticated edge. Every domain call goes through this typed
 * client, generated from the control plane's /openapi.json; domain types are
 * never hand-written. The auth bridge injects the deployment bearer plus
 * actor and tenant headers derived from the verified session, so every
 * mutation is attributed to the acting human in the runtime's audit trail.
 * When OIDC lands at the control-plane edge the headers disappear and
 * nothing else changes.
 */
import "server-only";

import createClient from "openapi-fetch";

import type { paths } from "./schema";

export interface ActorContext {
  /** Verified human identity, from the session; rides X-Actor-Id. */
  actorId: string;
  /** The active organization's runtime tenant id; rides X-Tenant-Id. */
  tenantId: string;
}

export function controlPlaneUrl(): string {
  return process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700";
}

function bridgeHeaders(actor: ActorContext): Record<string, string> {
  const token = process.env.CONVOY_CONTROL_PLANE_TOKEN;
  if (!token) {
    throw new Error("CONVOY_CONTROL_PLANE_TOKEN is not configured");
  }
  return {
    Authorization: `Bearer ${token}`,
    "X-Actor-Id": actor.actorId,
    "X-Tenant-Id": actor.tenantId,
  };
}

/** Typed control-plane client bound to one verified actor. */
export function controlPlane(actor: ActorContext) {
  return createClient<paths>({
    baseUrl: controlPlaneUrl(),
    headers: bridgeHeaders(actor),
  });
}

/** Header set for the SSE proxy route, which streams rather than fetches. */
export function sseHeaders(actor: ActorContext, lastEventId?: string): Record<string, string> {
  const headers = bridgeHeaders(actor);
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;
  return headers;
}

export type RunView =
  paths["/runs/{run_id}"]["get"]["responses"]["200"]["content"]["application/json"];
export type StepView = RunView["steps"][number];
export type BudgetView = NonNullable<RunView["budget"]>;
