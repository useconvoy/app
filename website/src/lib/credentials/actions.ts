/**
 * Admin server actions for model keys. Every action re-checks the admin gate
 * server-side (the UI lens is convenience only), validates the provider, and
 * does its work through the store, which is the only module that handles
 * ciphertext. Nothing here returns key material: the results carry a status,
 * an optional last four, and an optional reason code the form maps to copy.
 *
 * Setting a key verifies it against its provider first and stores it only if
 * it authenticates, so a key that cannot be used is never sealed. Removing a
 * key requires typing the provider name back, because removal fails any run
 * that is mid-flight on that key.
 */
"use server";

import { revalidatePath } from "next/cache";

import { requireOrgSession } from "@/lib/auth/session";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";
import { removeCredential, setCredential, verifyStoredCredential, type AdminContext } from "./store";
import { isProvider, verifyKey, type Provider } from "./verify";

/** Reason codes the form maps to copy; never key material. */
export type ModelKeyReason =
  | "unknown_provider"
  | "malformed"
  | "rejected"
  | "unreachable"
  | "not_configured"
  | "confirm_mismatch";

export interface ModelKeyResult {
  ok: boolean;
  last4?: string;
  reason?: ModelKeyReason;
}

/** Admin gate for model-key actions; throws for non-admins. */
async function requireCredentialAdmin(): Promise<AdminContext> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_members", membership.role, membership.capabilities)
  ) {
    throw new Error("Only organization admins can manage model keys");
  }
  return { orgId: session.orgId, userId: session.userId };
}

function revalidateModelAccess(): void {
  revalidatePath("/app/admin/model-access");
  revalidatePath("/app/admin");
}

/**
 * Verify a typed key against its provider and, only if it authenticates, seal
 * and store it. Returns the last four on success; on failure returns the
 * reason without ever echoing the key back.
 */
export async function setModelKeyAction(input: {
  provider: string;
  key: string;
}): Promise<ModelKeyResult> {
  const ctx = await requireCredentialAdmin();
  if (!isProvider(input.provider)) return { ok: false, reason: "unknown_provider" };
  const provider: Provider = input.provider;
  const key = typeof input.key === "string" ? input.key.trim() : "";
  if (key.length === 0) return { ok: false, reason: "malformed" };

  const verdict = await verifyKey(provider, key);
  if (!verdict.ok) {
    // "malformed" | "rejected" | "unreachable" all map to copy in the form.
    return { ok: false, reason: verdict.status };
  }

  const { last4 } = await setCredential(ctx, { provider, key });
  revalidateModelAccess();
  return { ok: true, last4 };
}

/**
 * Remove a provider's key after a typed confirmation of the provider name.
 * The confirmation guards against a removal that would fail in-flight runs.
 */
export async function removeModelKeyAction(input: {
  provider: string;
  confirm: string;
}): Promise<ModelKeyResult> {
  const ctx = await requireCredentialAdmin();
  if (!isProvider(input.provider)) return { ok: false, reason: "unknown_provider" };
  const provider: Provider = input.provider;
  const confirm = typeof input.confirm === "string" ? input.confirm.trim().toLowerCase() : "";
  if (confirm !== provider) return { ok: false, reason: "confirm_mismatch" };

  const removed = await removeCredential(ctx, provider);
  if (!removed) return { ok: false, reason: "not_configured" };
  revalidateModelAccess();
  return { ok: true };
}

/**
 * Re-check a stored key against its provider. The store unseals and verifies
 * server-side and records the outcome; this action only relays whether it
 * still authenticates.
 */
export async function verifyModelKeyAction(input: { provider: string }): Promise<ModelKeyResult> {
  const ctx = await requireCredentialAdmin();
  if (!isProvider(input.provider)) return { ok: false, reason: "unknown_provider" };
  const provider: Provider = input.provider;

  const result = await verifyStoredCredential(ctx, provider);
  if (!result.found) return { ok: false, reason: "not_configured" };
  revalidateModelAccess();
  if (result.status === "ok") return { ok: true };
  return { ok: false, reason: result.status };
}
