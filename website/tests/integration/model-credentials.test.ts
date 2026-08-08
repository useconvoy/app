// @vitest-environment node
/**
 * Model credentials against a real Postgres, through the same production
 * modules the server uses. Proves the one invariant end to end: only an admin
 * can read a credential row, a sealed key opens only under its own scope, and
 * the runtime issuance function reaches the row with no person in context but
 * only for the named org. Requires WEBSITE_PG_ADMIN_DSN (the migration owner)
 * and uses WEBSITE_PG_DSN for the RLS-bound app role; skips cleanly with no
 * database. Seeding of orgs/users/memberships and cleanup use the owner
 * connection; everything under test runs under its own RLS or system context.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { unseal } from "@/lib/credentials/crypto";
import {
  getCredentialStatus,
  issueCredential,
  setCredential,
} from "@/lib/credentials/store";
import { withOrgContext, withSystemContext } from "@/lib/db";
import { newTenantId } from "@/lib/orgs/validation";

const ADMIN_DSN = process.env.WEBSITE_PG_ADMIN_DSN;
const APP_DSN =
  process.env.WEBSITE_PG_DSN ??
  "postgresql://convoy_website_app:convoy_website_app@localhost:5440/convoy_website";
const WEBSITE_ROOT = new URL("../..", import.meta.url).pathname;

const run = randomUUID().slice(0, 8);
const orgA = randomUUID();
const orgB = randomUUID();
const adminA = randomUUID();
const memberA = randomUUID();
const adminB = randomUUID();

const ANTHROPIC_KEY = `sk-ant-${run}-abcdef0123456789`;
const OPENAI_KEY = `sk-${run}-9876543210fedcba`;

describe.skipIf(!ADMIN_DSN)("model credentials (real Postgres)", () => {
  let admin: pg.Client;

  beforeAll(async () => {
    execFileSync(process.execPath, ["scripts/migrate.mjs"], {
      cwd: WEBSITE_ROOT,
      env: { ...process.env, WEBSITE_PG_ADMIN_DSN: ADMIN_DSN as string },
      stdio: "pipe",
    });
    process.env.WEBSITE_PG_DSN = APP_DSN;
    // A valid 32-byte key-encryption key for the sealing paths under test.
    process.env.CONVOY_KEY_ENCRYPTION_KEY = "ab".repeat(32);

    admin = new pg.Client({ connectionString: ADMIN_DSN });
    await admin.connect();
    await admin.query(
      `INSERT INTO organizations (id, tenant_id, name) VALUES
        ($1, $3, 'Keys Org A ${run}'), ($2, $4, 'Keys Org B ${run}')`,
      [orgA, orgB, newTenantId(), newTenantId()],
    );
    await admin.query(
      `INSERT INTO users (id, email, name) VALUES
        ($1, 'ka-admin-${run}@example.com', 'K. Admin A'),
        ($2, 'ka-member-${run}@example.com', 'K. Member A'),
        ($3, 'kb-admin-${run}@example.com', 'K. Admin B')`,
      [adminA, memberA, adminB],
    );
    await admin.query(
      `INSERT INTO memberships (org_id, user_id, role) VALUES
        ($1, $3, 'admin'), ($1, $4, 'member'), ($2, $5, 'admin')`,
      [orgA, orgB, adminA, memberA, adminB],
    );
  }, 30_000);

  afterAll(async () => {
    await globalThis.__convoyWebsitePool?.end();
    globalThis.__convoyWebsitePool = undefined;
    if (!admin) return;
    await admin.query("DELETE FROM org_model_credentials WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM admin_audit WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM memberships WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM users WHERE id = ANY($1)", [[adminA, memberA, adminB]]);
    await admin.query("DELETE FROM organizations WHERE id = ANY($1)", [[orgA, orgB]]);
    await admin.end();
  });

  it("an admin sets a key and reads back only its status, never the key", async () => {
    const ctxA = { orgId: orgA, userId: adminA };
    const result = await setCredential(ctxA, { provider: "anthropic", key: ANTHROPIC_KEY });
    expect(result.last4).toBe(ANTHROPIC_KEY.slice(-4));

    const status = await getCredentialStatus(ctxA, "anthropic");
    expect(status).not.toBeNull();
    expect(status!.last4).toBe(ANTHROPIC_KEY.slice(-4));
    expect(status!.setByName).toBe("K. Admin A");
    expect(status!.verifyStatus).toBe("ok");
    // The status shape carries no field that could be the key.
    expect(Object.values(status!).join(" ")).not.toContain(ANTHROPIC_KEY);
  });

  it("a non-admin member of the same org cannot see the credential row", async () => {
    // As the admin: one row is visible.
    const asAdmin = await withOrgContext({ orgId: orgA, userId: adminA }, (client) =>
      client.query("SELECT provider FROM org_model_credentials"),
    );
    expect(asAdmin.rowCount).toBe(1);

    // As a plain member: the admin-only policy hides it entirely.
    const asMember = await withOrgContext({ orgId: orgA, userId: memberA }, (client) =>
      client.query("SELECT provider FROM org_model_credentials"),
    );
    expect(asMember.rowCount).toBe(0);

    // getCredentialStatus, which runs under the caller's context, also sees
    // nothing for the member.
    const memberStatus = await getCredentialStatus({ orgId: orgA, userId: memberA }, "anthropic");
    expect(memberStatus).toBeNull();
  });

  it("another org's admin cannot see this org's credential row", async () => {
    const fromB = await withOrgContext({ orgId: orgB, userId: adminB }, (client) =>
      client.query("SELECT provider FROM org_model_credentials WHERE org_id = $1", [orgA]),
    );
    expect(fromB.rowCount).toBe(0);
  });

  it("a stored ciphertext fails to unseal under another org's scope (transplant)", async () => {
    const sealed = await withOrgContext({ orgId: orgA, userId: adminA }, async (client) => {
      const { rows } = await client.query<{ ciphertext: Buffer }>(
        "SELECT ciphertext FROM org_model_credentials WHERE org_id = $1 AND provider = 'anthropic'",
        [orgA],
      );
      return rows[0]!.ciphertext;
    });
    // Under its own scope it opens.
    expect(unseal(sealed, { orgId: orgA, provider: "anthropic" })).toBe(ANTHROPIC_KEY);
    // Transplanted to another org it refuses.
    expect(() => unseal(sealed, { orgId: orgB, provider: "anthropic" })).toThrow();
    // Or another provider.
    expect(() => unseal(sealed, { orgId: orgA, provider: "openai" })).toThrow();
  });

  it("the issuance function returns the row with no person in context, only for the named org", async () => {
    const forA = await withSystemContext((client) =>
      client.query("SELECT ciphertext, kek_version FROM model_credential_for_issuance($1, $2)", [
        orgA,
        "anthropic",
      ]),
    );
    expect(forA.rowCount).toBe(1);

    // Org B has no anthropic key: the function returns nothing.
    const forB = await withSystemContext((client) =>
      client.query("SELECT ciphertext FROM model_credential_for_issuance($1, $2)", [
        orgB,
        "anthropic",
      ]),
    );
    expect(forB.rowCount).toBe(0);
  });

  it("issueCredential unseals and returns the key for the runtime, null when unset", async () => {
    const issued = await issueCredential({ orgId: orgA, provider: "anthropic", runId: `run-${run}` });
    expect(issued).toBe(ANTHROPIC_KEY);

    const missing = await issueCredential({ orgId: orgB, provider: "anthropic" });
    expect(missing).toBeNull();
  });

  it("fingerprint and last4 are stable across identical sets, and rotation replaces", async () => {
    const ctxA = { orgId: orgA, userId: adminA };
    const first = await getCredentialStatus(ctxA, "anthropic");
    // Set the same key again: fingerprint and last4 do not move.
    await setCredential(ctxA, { provider: "anthropic", key: ANTHROPIC_KEY });
    const again = await getCredentialStatus(ctxA, "anthropic");
    expect(again!.fingerprint).toBe(first!.fingerprint);
    expect(again!.last4).toBe(first!.last4);

    // Rotate to a different key: fingerprint and last4 change.
    await setCredential(ctxA, { provider: "anthropic", key: OPENAI_KEY });
    const rotated = await getCredentialStatus(ctxA, "anthropic");
    expect(rotated!.fingerprint).not.toBe(first!.fingerprint);
    expect(rotated!.last4).toBe(OPENAI_KEY.slice(-4));
    // Reset for any later reads.
    await setCredential(ctxA, { provider: "anthropic", key: ANTHROPIC_KEY });
  });
});
