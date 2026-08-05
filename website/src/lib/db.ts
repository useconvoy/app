/**
 * Website database access. Every query runs inside a transaction that first
 * sets the org (and usually user) context RLS keys off; the pool connects
 * as the RLS-bound app role, so a query without context sees no rows. Org
 * context always derives from the verified session, never from client input.
 */
import { Pool, type PoolClient } from "pg";

declare global {
  var __convoyWebsitePool: Pool | undefined;
}

function pool(): Pool {
  if (!globalThis.__convoyWebsitePool) {
    const connectionString = process.env.WEBSITE_PG_DSN;
    if (!connectionString) {
      throw new Error("WEBSITE_PG_DSN is not configured");
    }
    globalThis.__convoyWebsitePool = new Pool({ connectionString, max: 10 });
  }
  return globalThis.__convoyWebsitePool;
}

export interface DbContext {
  orgId: string;
  userId?: string;
}

/**
 * Run `fn` in a transaction carrying org/user RLS context. The context GUCs
 * are transaction-local (`set_config(..., true)`), so nothing leaks back to
 * the pool.
 */
export async function withOrgContext<T>(
  context: DbContext,
  fn: (client: PoolClient) => Promise<T>,
): Promise<T> {
  const client = await pool().connect();
  try {
    await client.query("BEGIN");
    await client.query("SELECT set_config('app.org_id', $1, true), set_config('app.user_id', $2, true)", [
      context.orgId,
      context.userId ?? "",
    ]);
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}

/**
 * Pre-org context for the narrow flows that legitimately run before an
 * active org exists: identity sync at sign-in, org creation, listing the
 * user's own memberships for the switcher. User context only; RLS still
 * bounds visibility to the user's own rows.
 */
export async function withUserContext<T>(
  userId: string,
  fn: (client: PoolClient) => Promise<T>,
): Promise<T> {
  const client = await pool().connect();
  try {
    await client.query("BEGIN");
    await client.query("SELECT set_config('app.org_id', '', true), set_config('app.user_id', $1, true)", [
      userId,
    ]);
    const result = await fn(client);
    await client.query("COMMIT");
    return result;
  } catch (error) {
    await client.query("ROLLBACK").catch(() => undefined);
    throw error;
  } finally {
    client.release();
  }
}
