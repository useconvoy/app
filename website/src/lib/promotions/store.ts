/**
 * Promotion requests (C8): a rehearsal run's land report submitted for a
 * person to review before anything goes live. The request is website-side
 * state, keyed org + routine + run so resubmitting is idempotent.
 *
 * Storage is in-process for W2, like the run directory it accompanies.
 * TODO(website): decide durable storage (a website table under RLS, or a
 * runtime-side promotion object) before multi-instance deployment; the
 * accessors below are the seam.
 */
import "server-only";

import { randomUUID } from "node:crypto";

export type PromotionRequestStatus = "requested" | "promoted" | "sent_back";

export interface PromotionRequest {
  id: string;
  orgId: string;
  routineId: string;
  runId: string;
  status: PromotionRequestStatus;
  requestedBy: string;
  requestedAt: string;
  decidedBy: string | null;
  decidedAt: string | null;
  /** The send-back note: what should change before this goes live. */
  note: string | null;
}

declare global {
  var __convoyPromotionStore: Map<string, PromotionRequest> | undefined;
}

function store(): Map<string, PromotionRequest> {
  if (!globalThis.__convoyPromotionStore) {
    globalThis.__convoyPromotionStore = new Map();
  }
  return globalThis.__convoyPromotionStore;
}

function keyOf(orgId: string, routineId: string, runId: string): string {
  return `${orgId}:${routineId}:${runId}`;
}

/**
 * Record (or re-open) a promotion request. Submitting the same run again
 * while a request is open returns the existing request unchanged; a
 * sent-back request re-opens with a fresh stamp.
 */
export function requestPromotion(input: {
  orgId: string;
  routineId: string;
  runId: string;
  requestedBy: string;
}): PromotionRequest {
  const key = keyOf(input.orgId, input.routineId, input.runId);
  const existing = store().get(key);
  if (existing && existing.status === "requested") return existing;
  const request: PromotionRequest = {
    id: existing?.id ?? randomUUID(),
    orgId: input.orgId,
    routineId: input.routineId,
    runId: input.runId,
    status: "requested",
    requestedBy: input.requestedBy,
    requestedAt: new Date().toISOString(),
    decidedBy: null,
    decidedAt: null,
    note: null,
  };
  store().set(key, request);
  return request;
}

/** A request by id, org-scoped: another org's id is simply not found. */
export function getPromotionRequest(orgId: string, requestId: string): PromotionRequest | null {
  for (const request of store().values()) {
    if (request.orgId === orgId && request.id === requestId) return request;
  }
  return null;
}

/** The request covering one run, if any. */
export function promotionRequestForRun(
  orgId: string,
  routineId: string,
  runId: string,
): PromotionRequest | null {
  return store().get(keyOf(orgId, routineId, runId)) ?? null;
}

/** All of an org's requests, newest first. */
export function listPromotionRequests(orgId: string): PromotionRequest[] {
  return [...store().values()]
    .filter((request) => request.orgId === orgId)
    .sort((a, b) => b.requestedAt.localeCompare(a.requestedAt));
}

/** Settle a request. Returns null when it is not found or already settled. */
export function decidePromotionRequest(
  orgId: string,
  requestId: string,
  decision: "promoted" | "sent_back",
  decidedBy: string,
  note?: string,
): PromotionRequest | null {
  const request = getPromotionRequest(orgId, requestId);
  if (!request || request.status !== "requested") return null;
  const settled: PromotionRequest = {
    ...request,
    status: decision,
    decidedBy,
    decidedAt: new Date().toISOString(),
    note: note ?? null,
  };
  store().set(keyOf(request.orgId, request.routineId, request.runId), settled);
  return settled;
}
