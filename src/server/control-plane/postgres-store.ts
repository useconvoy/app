import { Pool } from "pg";
import type {
  Account,
  Mission,
  Session,
  Workspace,
  WorkspaceMembership,
  WorkspaceRole,
} from "./types";

const connectionString = process.env.DATABASE_URL;
const globalDatabase = globalThis as unknown as {
  __convoyPgPool?: Pool;
  __convoyPgSchema?: Promise<void>;
};

function pool(): Pool {
  if (!connectionString) {
    throw new Error("DATABASE_URL is not configured.");
  }
  if (!globalDatabase.__convoyPgPool) {
    const local =
      connectionString.includes("localhost") ||
      connectionString.includes("127.0.0.1");
    globalDatabase.__convoyPgPool = new Pool({
      connectionString,
      max: 4,
      idleTimeoutMillis: 30_000,
      connectionTimeoutMillis: 10_000,
      ssl: local ? undefined : { rejectUnauthorized: false },
    });
  }
  return globalDatabase.__convoyPgPool;
}

async function ensureSchema(): Promise<void> {
  if (!globalDatabase.__convoyPgSchema) {
    globalDatabase.__convoyPgSchema = pool()
      .query(`
        CREATE TABLE IF NOT EXISTS convoy_accounts (
          id TEXT PRIMARY KEY,
          email TEXT NOT NULL UNIQUE,
          name TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL
        );

        CREATE TABLE IF NOT EXISTS convoy_sessions (
          token_hash TEXT PRIMARY KEY,
          account_id TEXT NOT NULL REFERENCES convoy_accounts(id) ON DELETE CASCADE,
          created_at TIMESTAMPTZ NOT NULL,
          expires_at TIMESTAMPTZ NOT NULL
        );
        CREATE INDEX IF NOT EXISTS convoy_sessions_account_idx
          ON convoy_sessions(account_id);

        CREATE TABLE IF NOT EXISTS convoy_workspaces (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          slug TEXT NOT NULL UNIQUE,
          created_at TIMESTAMPTZ NOT NULL,
          created_by_account_id TEXT NOT NULL REFERENCES convoy_accounts(id)
        );

        CREATE TABLE IF NOT EXISTS convoy_workspace_memberships (
          account_id TEXT NOT NULL REFERENCES convoy_accounts(id) ON DELETE CASCADE,
          workspace_id TEXT NOT NULL REFERENCES convoy_workspaces(id) ON DELETE CASCADE,
          role TEXT NOT NULL,
          created_at TIMESTAMPTZ NOT NULL,
          PRIMARY KEY (account_id, workspace_id)
        );
        CREATE INDEX IF NOT EXISTS convoy_memberships_workspace_idx
          ON convoy_workspace_memberships(workspace_id);

        CREATE TABLE IF NOT EXISTS convoy_missions (
          id TEXT PRIMARY KEY,
          workspace_id TEXT NOT NULL REFERENCES convoy_workspaces(id) ON DELETE CASCADE,
          created_by_account_id TEXT NOT NULL REFERENCES convoy_accounts(id),
          objective TEXT NOT NULL,
          state TEXT NOT NULL,
          provider TEXT NOT NULL,
          envelope JSONB NOT NULL,
          provider_task_arn TEXT,
          summary TEXT,
          total_agents INTEGER,
          created_at TIMESTAMPTZ NOT NULL,
          updated_at TIMESTAMPTZ NOT NULL,
          completed_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS convoy_missions_workspace_created_idx
          ON convoy_missions(workspace_id, created_at DESC);
      `)
      .then(() => undefined);
  }
  await globalDatabase.__convoyPgSchema;
}

function accountFromRow(row: Record<string, unknown>): Account {
  return {
    id: String(row.id),
    email: String(row.email),
    name: String(row.name),
    passwordHash: String(row.password_hash),
    createdAt: new Date(String(row.created_at)).toISOString(),
  };
}

function workspaceFromRow(row: Record<string, unknown>): Workspace {
  return {
    id: String(row.id),
    name: String(row.name),
    slug: String(row.slug),
    createdAt: new Date(String(row.created_at)).toISOString(),
    createdByAccountId: String(row.created_by_account_id),
  };
}

function membershipFromRow(
  row: Record<string, unknown>,
): WorkspaceMembership {
  return {
    accountId: String(row.account_id),
    workspaceId: String(row.workspace_id),
    role: String(row.role) as WorkspaceRole,
    createdAt: new Date(String(row.membership_created_at ?? row.created_at))
      .toISOString(),
  };
}

function missionFromRow(row: Record<string, unknown>): Mission {
  return {
    id: String(row.id),
    workspaceId: String(row.workspace_id),
    createdByAccountId: String(row.created_by_account_id),
    objective: String(row.objective),
    state: String(row.state) as Mission["state"],
    provider: String(row.provider) as Mission["provider"],
    envelope: row.envelope as Mission["envelope"],
    providerTaskArn: row.provider_task_arn
      ? String(row.provider_task_arn)
      : undefined,
    summary: row.summary ? String(row.summary) : undefined,
    totalAgents:
      row.total_agents === null || row.total_agents === undefined
        ? undefined
        : Number(row.total_agents),
    createdAt: new Date(String(row.created_at)).toISOString(),
    updatedAt: new Date(String(row.updated_at)).toISOString(),
    completedAt: row.completed_at
      ? new Date(String(row.completed_at)).toISOString()
      : undefined,
  };
}

export function enabled(): boolean {
  return Boolean(connectionString);
}

export async function createAccount(account: Account): Promise<void> {
  await ensureSchema();
  await pool().query(
    `INSERT INTO convoy_accounts
       (id, email, name, password_hash, created_at)
     VALUES ($1, $2, $3, $4, $5)`,
    [
      account.id,
      account.email.toLowerCase(),
      account.name,
      account.passwordHash,
      account.createdAt,
    ],
  );
}

export async function getAccount(accountId: string): Promise<Account | null> {
  await ensureSchema();
  const result = await pool().query(
    "SELECT * FROM convoy_accounts WHERE id = $1",
    [accountId],
  );
  return result.rows[0] ? accountFromRow(result.rows[0]) : null;
}

export async function getAccountByEmail(
  email: string,
): Promise<Account | null> {
  await ensureSchema();
  const result = await pool().query(
    "SELECT * FROM convoy_accounts WHERE email = $1",
    [email.toLowerCase()],
  );
  return result.rows[0] ? accountFromRow(result.rows[0]) : null;
}

export async function createSession(session: Session): Promise<void> {
  await ensureSchema();
  await pool().query(
    `INSERT INTO convoy_sessions
       (token_hash, account_id, created_at, expires_at)
     VALUES ($1, $2, $3, $4)
     ON CONFLICT (token_hash) DO UPDATE SET
       account_id = EXCLUDED.account_id,
       created_at = EXCLUDED.created_at,
       expires_at = EXCLUDED.expires_at`,
    [
      session.tokenHash,
      session.accountId,
      session.createdAt,
      session.expiresAt,
    ],
  );
}

export async function getSession(tokenHash: string): Promise<Session | null> {
  await ensureSchema();
  const result = await pool().query(
    "SELECT * FROM convoy_sessions WHERE token_hash = $1",
    [tokenHash],
  );
  const row = result.rows[0];
  if (!row) return null;
  return {
    tokenHash: String(row.token_hash),
    accountId: String(row.account_id),
    createdAt: new Date(String(row.created_at)).toISOString(),
    expiresAt: new Date(String(row.expires_at)).toISOString(),
  };
}

export async function deleteSession(tokenHash: string): Promise<void> {
  await ensureSchema();
  await pool().query("DELETE FROM convoy_sessions WHERE token_hash = $1", [
    tokenHash,
  ]);
}

export async function createWorkspace(
  workspace: Workspace,
  membership: WorkspaceMembership,
): Promise<void> {
  await ensureSchema();
  const client = await pool().connect();
  try {
    await client.query("BEGIN");
    await client.query(
      `INSERT INTO convoy_workspaces
         (id, name, slug, created_at, created_by_account_id)
       VALUES ($1, $2, $3, $4, $5)`,
      [
        workspace.id,
        workspace.name,
        workspace.slug,
        workspace.createdAt,
        workspace.createdByAccountId,
      ],
    );
    await client.query(
      `INSERT INTO convoy_workspace_memberships
         (account_id, workspace_id, role, created_at)
       VALUES ($1, $2, $3, $4)`,
      [
        membership.accountId,
        membership.workspaceId,
        membership.role,
        membership.createdAt,
      ],
    );
    await client.query("COMMIT");
  } catch (error) {
    await client.query("ROLLBACK");
    throw error;
  } finally {
    client.release();
  }
}

export async function getWorkspace(
  workspaceId: string,
): Promise<Workspace | null> {
  await ensureSchema();
  const result = await pool().query(
    "SELECT * FROM convoy_workspaces WHERE id = $1",
    [workspaceId],
  );
  return result.rows[0] ? workspaceFromRow(result.rows[0]) : null;
}

export async function getMembership(
  accountId: string,
  workspaceId: string,
): Promise<WorkspaceMembership | null> {
  await ensureSchema();
  const result = await pool().query(
    `SELECT account_id, workspace_id, role, created_at
       FROM convoy_workspace_memberships
      WHERE account_id = $1 AND workspace_id = $2`,
    [accountId, workspaceId],
  );
  return result.rows[0] ? membershipFromRow(result.rows[0]) : null;
}

export async function listWorkspacesForAccount(
  accountId: string,
): Promise<Array<Workspace & { role: WorkspaceRole }>> {
  await ensureSchema();
  const result = await pool().query(
    `SELECT w.*, m.role, m.created_at AS membership_created_at
       FROM convoy_workspace_memberships m
       JOIN convoy_workspaces w ON w.id = m.workspace_id
      WHERE m.account_id = $1
      ORDER BY w.created_at ASC`,
    [accountId],
  );
  return result.rows.map((row) => ({
    ...workspaceFromRow(row),
    role: String(row.role) as WorkspaceRole,
  }));
}

export async function createMission(mission: Mission): Promise<void> {
  await ensureSchema();
  await pool().query(
    `INSERT INTO convoy_missions
       (id, workspace_id, created_by_account_id, objective, state, provider,
        envelope, provider_task_arn, summary, total_agents, created_at,
        updated_at, completed_at)
     VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10, $11, $12, $13)`,
    [
      mission.id,
      mission.workspaceId,
      mission.createdByAccountId,
      mission.objective,
      mission.state,
      mission.provider,
      JSON.stringify(mission.envelope),
      mission.providerTaskArn ?? null,
      mission.summary ?? null,
      mission.totalAgents ?? null,
      mission.createdAt,
      mission.updatedAt,
      mission.completedAt ?? null,
    ],
  );
}

export async function getMission(missionId: string): Promise<Mission | null> {
  await ensureSchema();
  const result = await pool().query(
    "SELECT * FROM convoy_missions WHERE id = $1",
    [missionId],
  );
  return result.rows[0] ? missionFromRow(result.rows[0]) : null;
}

export async function updateMission(mission: Mission): Promise<void> {
  await ensureSchema();
  await pool().query(
    `UPDATE convoy_missions SET
       state = $2,
       provider = $3,
       envelope = $4::jsonb,
       provider_task_arn = $5,
       summary = $6,
       total_agents = $7,
       updated_at = $8,
       completed_at = $9
     WHERE id = $1`,
    [
      mission.id,
      mission.state,
      mission.provider,
      JSON.stringify(mission.envelope),
      mission.providerTaskArn ?? null,
      mission.summary ?? null,
      mission.totalAgents ?? null,
      mission.updatedAt,
      mission.completedAt ?? null,
    ],
  );
}

export async function listMissions(workspaceId: string): Promise<Mission[]> {
  await ensureSchema();
  const result = await pool().query(
    `SELECT * FROM convoy_missions
      WHERE workspace_id = $1
      ORDER BY created_at DESC`,
    [workspaceId],
  );
  return result.rows.map(missionFromRow);
}
