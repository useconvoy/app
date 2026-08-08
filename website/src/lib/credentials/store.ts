/**
 * The store for customer model keys: the only module besides crypto that
 * handles ciphertext, and the boundary that keeps the one invariant true. A
 * key can be set, rotated, verified, removed, and issued to a run, and at no
 * point does any function here return the plaintext to a caller that would
 * carry it to a browser. The admin paths hand back only a status (provider,
 * last four, fingerprint, who set it, when, and its last verification); the
 * issuance path hands back the plaintext, and only to the runtime over the
 * internal service boundary.
 *
 * Every admin path runs under withOrgContext with the session's org and user,
 * so the credential_admin_only policy (migration 0008) enforces an active
 * admin membership in the database even to read the ciphertext. The issuance
 * path has no person, so it runs under withSystemContext and reaches the row
 * only through the SECURITY DEFINER function that returns nothing but the two
 * columns needed to unseal it.
 */
import "server-only";

import type { PoolClient } from "pg";

import { withOrgContext, withSystemContext, type DbContext } from "@/lib/db";
import { fingerprint, KEK_VERSION, last4, seal, unseal } from "./crypto";
import { isProvider, verifyKey, type Provider, type VerifyStatus } from "./verify";

/** An admin acting on a credential: the org and the verified admin's id. */
export type AdminContext = Required<DbContext>;

export interface CredentialStatus {
  provider: Provider;
  /** The last four characters of the key, the one fragment ever shown. */
  last4: string;
  /** sha256 of the key; identifies it without revealing it. */
  fingerprint: string;
  /** Name of the admin who set the key, or null if they have left the org. */
  setByName: string | null;
  setAt: Date;
  verifiedAt: Date | null;
  /** The recorded outcome code of the last verification, or null. */
  verifyStatus: VerifyStatus | null;
}

/** Write a key-free admin_audit row inside the caller's transaction. */
async function auditModelKey(
  client: PoolClient,
  ctx: AdminContext,
  action: string,
  provider: Provider,
): Promise<void> {
  // The subject is the provider name only. It never carries key material, not
  // even the last four, so the audit trail can be read by anyone without
  // leaking anything about the key itself.
  await client.query(
    "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
    [ctx.orgId, ctx.userId, action, provider],
  );
}

/**
 * Seal and store a key for a provider, replacing any existing one (a rotation
 * is the same upsert). The precondition is that the caller has already
 * verified the key against its provider, so the row is written as verified
 * now; a key that could not authenticate never reaches here. Returns the last
 * four for the confirmation message, never the key.
 */
export async function setCredential(
  ctx: AdminContext,
  input: { provider: Provider; key: string },
): Promise<{ last4: string }> {
  const key = input.key.trim();
  const sealed = seal(key, { orgId: ctx.orgId, provider: input.provider });
  const fp = fingerprint(key);
  const tail = last4(key);
  await withOrgContext(ctx, async (client) => {
    await client.query(
      `INSERT INTO org_model_credentials
         (org_id, provider, ciphertext, kek_version, key_fingerprint, key_last4,
          set_by, set_at, verified_at, verify_status)
       VALUES ($1, $2, $3, $4, $5, $6, $7, now(), now(), 'ok')
       ON CONFLICT (org_id, provider) DO UPDATE SET
         ciphertext = EXCLUDED.ciphertext,
         kek_version = EXCLUDED.kek_version,
         key_fingerprint = EXCLUDED.key_fingerprint,
         key_last4 = EXCLUDED.key_last4,
         set_by = EXCLUDED.set_by,
         set_at = now(),
         verified_at = now(),
         verify_status = 'ok'`,
      [ctx.orgId, input.provider, sealed, KEK_VERSION, fp, tail, ctx.userId],
    );
    await auditModelKey(client, ctx, "model_key.set", input.provider);
  });
  return { last4: tail };
}

/** Remove a provider's key. A no-op returns false so the caller can report it. */
export async function removeCredential(ctx: AdminContext, provider: Provider): Promise<boolean> {
  return withOrgContext(ctx, async (client) => {
    const result = await client.query(
      "DELETE FROM org_model_credentials WHERE org_id = $1 AND provider = $2",
      [ctx.orgId, provider],
    );
    if (result.rowCount === 0) return false;
    await auditModelKey(client, ctx, "model_key.removed", provider);
    return true;
  });
}

/** The admin-facing status of a provider's key, or null when none is set. */
export async function getCredentialStatus(
  ctx: AdminContext,
  provider: Provider,
): Promise<CredentialStatus | null> {
  return withOrgContext(ctx, async (client) => {
    const { rows } = await client.query<{
      provider: Provider;
      last4: string;
      fingerprint: string;
      setByName: string | null;
      setAt: Date;
      verifiedAt: Date | null;
      verifyStatus: VerifyStatus | null;
    }>(
      `SELECT c.provider,
              c.key_last4 AS "last4",
              c.key_fingerprint AS "fingerprint",
              u.name AS "setByName",
              c.set_at AS "setAt",
              c.verified_at AS "verifiedAt",
              c.verify_status AS "verifyStatus"
         FROM org_model_credentials c
         LEFT JOIN users u ON u.id = c.set_by
        WHERE c.org_id = $1 AND c.provider = $2`,
      [ctx.orgId, provider],
    );
    return rows[0] ?? null;
  });
}

/**
 * Re-verify a stored key against its provider without ever showing it. The
 * ciphertext is read under the admin context, unsealed in this process,
 * checked over the network, and the outcome recorded; the plaintext never
 * leaves this function. Returns whether a key was present and the outcome.
 */
export async function verifyStoredCredential(
  ctx: AdminContext,
  provider: Provider,
): Promise<{ found: false } | { found: true; status: VerifyStatus }> {
  const sealed = await withOrgContext(ctx, async (client) => {
    const { rows } = await client.query<{ ciphertext: Buffer }>(
      "SELECT ciphertext FROM org_model_credentials WHERE org_id = $1 AND provider = $2",
      [ctx.orgId, provider],
    );
    return rows[0]?.ciphertext ?? null;
  });
  if (!sealed) return { found: false };

  const key = unseal(sealed, { orgId: ctx.orgId, provider });
  const result = await verifyKey(provider, key);

  await withOrgContext(ctx, async (client) => {
    await client.query(
      `UPDATE org_model_credentials
          SET verified_at = now(), verify_status = $3
        WHERE org_id = $1 AND provider = $2`,
      [ctx.orgId, provider, result.status],
    );
    await auditModelKey(client, ctx, "model_key.verified", provider);
  });
  return { found: true, status: result.status };
}

/**
 * Issue a run its organization's key. This is the one place plaintext leaves
 * the process, and only to the runtime: the caller (the machine-authenticated
 * issuance endpoint) has already established that the run belongs to this org.
 * The row is reached through the SECURITY DEFINER function under a no-person
 * system context; a row sealed under a key version this build cannot key is
 * refused rather than opened. Returns null when the org has no key for the
 * provider. The issuance is recorded as a key-free log line rather than an
 * admin_audit row, because that table attributes every action to a human
 * actor and issuance has none.
 */
export async function issueCredential(input: {
  orgId: string;
  provider: Provider;
  /** The run the key is being issued to, for the issuance log only. */
  runId?: string;
}): Promise<string | null> {
  if (!isProvider(input.provider)) return null;
  const row = await withSystemContext(async (client) => {
    const { rows } = await client.query<{ ciphertext: Buffer; kek_version: number }>(
      "SELECT ciphertext, kek_version FROM model_credential_for_issuance($1, $2)",
      [input.orgId, input.provider],
    );
    return rows[0] ?? null;
  });
  if (!row) return null;
  if (row.kek_version !== KEK_VERSION) {
    // Fail closed: a row we cannot key must not be guessed at.
    throw new Error("stored key was sealed under an unsupported key version");
  }
  const key = unseal(row.ciphertext, { orgId: input.orgId, provider: input.provider });
  // Key-free issuance record. Never the key, only who it was for and when.
  console.info("model_key.issued", {
    orgId: input.orgId,
    provider: input.provider,
    runId: input.runId,
    kekVersion: row.kek_version,
  });
  return key;
}
