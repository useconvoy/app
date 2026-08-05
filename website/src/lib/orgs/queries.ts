/**
 * Server-only data access for identity and org structure. Every query runs
 * through withOrgContext/withUserContext so RLS bounds it; org ids arrive
 * from the verified session (callers), never from client input.
 */
import "server-only";

import { withOrgContext, withUserContext } from "@/lib/db";
import { requireOrgSession } from "@/lib/auth/session";
import type { Role } from "@/lib/permissions";

export interface SyncedUser {
  id: string;
  email: string;
  name: string;
}

/**
 * Sign-in identity sync: upsert the users row by email. Runs before any
 * user context exists (we only learn the id from this call), so it rides
 * the SECURITY DEFINER sync_user_identity function from migration 0002
 * under an empty user context.
 */
export async function syncUser(identity: {
  email: string;
  name: string;
  workosUserId?: string;
}): Promise<SyncedUser> {
  return withUserContext("", async (client) => {
    const { rows } = await client.query<SyncedUser>(
      "SELECT id, email, name FROM sync_user_identity($1, $2, $3)",
      [identity.email, identity.name, identity.workosUserId ?? null],
    );
    const user = rows[0];
    if (!user) throw new Error("identity sync returned no row");
    return user;
  });
}

export interface UserOrg {
  id: string;
  name: string;
  tenantId: string;
  role: Role;
}

/** The user's organizations with their role, for the switcher. */
export async function listUserOrgs(userId: string): Promise<UserOrg[]> {
  return withUserContext(userId, async (client) => {
    const { rows } = await client.query<UserOrg>(
      `SELECT o.id, o.name, o.tenant_id AS "tenantId", m.role
         FROM memberships m
         JOIN organizations o ON o.id = m.org_id
        WHERE m.user_id = $1 AND m.status = 'active'
        ORDER BY o.name`,
      [userId],
    );
    return rows;
  });
}

export interface Membership {
  role: Role;
  capabilities: string[];
  status: string;
}

/** The user's membership in an org, or null; the server-side role check. */
export async function getMembership(orgId: string, userId: string): Promise<Membership | null> {
  return withOrgContext({ orgId, userId }, async (client) => {
    const { rows } = await client.query<Membership>(
      "SELECT role, capabilities, status FROM memberships WHERE org_id = $1 AND user_id = $2",
      [orgId, userId],
    );
    return rows[0] ?? null;
  });
}

export interface MemberRow {
  userId: string;
  name: string;
  email: string;
  role: Role;
  capabilities: string[];
  status: string;
}

export async function listMembers(orgId: string): Promise<MemberRow[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<MemberRow>(
      `SELECT m.user_id AS "userId", u.name, u.email, m.role, m.capabilities, m.status
         FROM memberships m
         JOIN users u ON u.id = m.user_id
        WHERE m.org_id = $1
        ORDER BY u.name, u.email`,
      [orgId],
    );
    return rows;
  });
}

export interface TeamRow {
  id: string;
  name: string;
  memberCount: number;
}

export async function listTeams(orgId: string): Promise<TeamRow[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<TeamRow>(
      `SELECT t.id, t.name,
              (SELECT count(*)::int FROM team_memberships tm WHERE tm.team_id = t.id) AS "memberCount"
         FROM teams t
        WHERE t.org_id = $1
        ORDER BY t.name`,
      [orgId],
    );
    return rows;
  });
}

export interface TeamMemberRow {
  userId: string;
  name: string;
  email: string;
}

/** Members of one team. Org context comes from the verified session. */
export async function listTeamMembers(teamId: string): Promise<TeamMemberRow[]> {
  const session = await requireOrgSession();
  return withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const { rows } = await client.query<TeamMemberRow>(
      `SELECT u.id AS "userId", u.name, u.email
         FROM team_memberships tm
         JOIN users u ON u.id = tm.user_id
        WHERE tm.team_id = $1
        ORDER BY u.name, u.email`,
      [teamId],
    );
    return rows;
  });
}

export interface InviteRow {
  id: string;
  email: string;
  role: Role;
  token: string;
  status: string;
  expiresAt: Date;
  createdAt: Date;
}

export async function listInvites(orgId: string): Promise<InviteRow[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<InviteRow>(
      `SELECT id, email, role, token, status,
              expires_at AS "expiresAt", created_at AS "createdAt"
         FROM invites
        WHERE org_id = $1
        ORDER BY created_at DESC`,
      [orgId],
    );
    return rows;
  });
}

export interface InvitePreview {
  id: string;
  orgId: string;
  orgName: string;
  email: string;
  role: Role;
  status: string;
  expiresAt: Date;
}

export interface AuditEntryRow {
  id: string;
  action: string;
  subject: string;
  ts: Date;
  /** Resolved actor name; null when the actor left the org. */
  actorName: string | null;
}

export interface AuditPage {
  entries: AuditEntryRow[];
  hasMore: boolean;
}

/**
 * The org audit surface (DESIGN §5 Admin row, §9): admin_audit newest
 * first, filterable by action prefix, actor names resolved through the
 * shared-org users policy. Simple limit/offset pagination; one extra row
 * is fetched to learn whether an older page exists.
 */
export async function listAuditEntries(
  orgId: string,
  options: { actionPrefix?: string; limit?: number; offset?: number } = {},
): Promise<AuditPage> {
  const limit = Math.min(Math.max(options.limit ?? 50, 1), 200);
  const offset = Math.max(options.offset ?? 0, 0);
  const prefix = options.actionPrefix?.trim() || null;
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<AuditEntryRow>(
      `SELECT a.id, a.action, a.subject, a.ts, u.name AS "actorName"
         FROM admin_audit a
         LEFT JOIN users u ON u.id = a.actor_id
        WHERE a.org_id = $1
          AND ($2::text IS NULL OR a.action LIKE $2 || '%')
        ORDER BY a.ts DESC
        LIMIT $3 OFFSET $4`,
      [orgId, prefix, limit + 1, offset],
    );
    return { entries: rows.slice(0, limit), hasMore: rows.length > limit };
  });
}

/**
 * Resolve an invite link for the signed-in user, before membership exists.
 * Visibility is scoped to the presented token via the transaction-local
 * app.invite_token GUC (migration 0002); no cross-org probing is possible.
 */
export async function getInviteForToken(token: string, userId: string): Promise<InvitePreview | null> {
  return withUserContext(userId, async (client) => {
    await client.query("SELECT set_config('app.invite_token', $1, true)", [token]);
    const { rows } = await client.query<InvitePreview>(
      `SELECT i.id, i.org_id AS "orgId", o.name AS "orgName", i.email, i.role,
              i.status, i.expires_at AS "expiresAt"
         FROM invites i
         JOIN organizations o ON o.id = i.org_id
        WHERE i.token = $1`,
      [token],
    );
    return rows[0] ?? null;
  });
}
