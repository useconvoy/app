/**
 * Server actions for org structure and admin management. Every action
 * derives org and actor from the verified session (never from client
 * input), re-checks the permissions matrix server-side, and writes an
 * admin_audit row for administrative mutations inside the same
 * transaction. The UI lens hiding these controls is convenience only.
 */
"use server";

import { randomUUID } from "node:crypto";
import type { PoolClient } from "pg";
import { revalidatePath } from "next/cache";

import { provisionEnvironmentsOrg, syncInviteToEnvironments } from "@/lib/api/environments";
import { requireOrgSession, requireSession, setActiveOrg } from "@/lib/auth/session";
import { withOrgContext, withUserContext } from "@/lib/db";
import { can } from "@/lib/permissions";
import { getMembership } from "./queries";
import {
  inviteExpiry,
  inviteVerdict,
  isEmail,
  isInviteToken,
  isRole,
  newInviteToken,
  newTenantId,
  normalizeEmail,
  parseCapabilities,
  type InviteVerdict,
} from "./validation";

const ADMIN_PATHS = ["/app/admin", "/app/admin/members", "/app/admin/teams"];

function revalidateAdmin(): void {
  for (const path of ADMIN_PATHS) revalidatePath(path);
}

async function audit(
  client: PoolClient,
  orgId: string,
  actorId: string,
  action: string,
  subject: string,
): Promise<void> {
  await client.query(
    "INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)",
    [orgId, actorId, action, subject],
  );
}

/** Session + active-role gate for admin-only actions; throws otherwise. */
async function requireAdmin() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_members", membership.role, membership.capabilities)
  ) {
    throw new Error("Only organization admins can do that");
  }
  return { session, membership };
}

/**
 * Create an organization with the current user as its admin and make it
 * the session's active org. The org id is generated here so the whole
 * creation runs in one transaction under the new org's RLS context.
 */
export async function createOrganization(name: string): Promise<{ id: string }> {
  const session = await requireSession();
  const trimmed = name.trim();
  if (trimmed.length < 2 || trimmed.length > 120) {
    throw new Error("Organization name must be between 2 and 120 characters");
  }
  const orgId = randomUUID();
  await withOrgContext({ orgId, userId: session.userId }, async (client) => {
    await client.query("INSERT INTO organizations (id, tenant_id, name) VALUES ($1, $2, $3)", [
      orgId,
      newTenantId(),
      trimmed,
    ]);
    await client.query(
      "INSERT INTO memberships (org_id, user_id, role, status) VALUES ($1, $2, 'admin', 'active')",
      [orgId, session.userId],
    );
    await audit(client, orgId, session.userId, "organization.created", orgId);
  });
  // Mirror the org into the environments registry when the E0 adapter is
  // switched on; the stored id keys every later registry call. Best-effort
  // inside: a registry failure leaves this org table-backed, never blocks
  // creation.
  await provisionEnvironmentsOrg({ orgId, name: trimmed, creatorEmail: session.email });
  await setActiveOrg(orgId);
  return { id: orgId };
}

/** Switch the active org after verifying the user's own membership. */
export async function switchOrganization(orgId: string): Promise<void> {
  const session = await requireSession();
  const membership = await withUserContext(session.userId, async (client) => {
    const { rows } = await client.query<{ status: string }>(
      "SELECT status FROM memberships WHERE org_id = $1 AND user_id = $2",
      [orgId, session.userId],
    );
    return rows[0] ?? null;
  });
  if (!membership || membership.status !== "active") {
    throw new Error("You are not a member of that organization");
  }
  await setActiveOrg(orgId);
}

/** Admin-only: create a seven-day invite for an email and role. */
export async function inviteMember(email: string, role: string): Promise<{ id: string }> {
  const { session } = await requireAdmin();
  const normalized = normalizeEmail(email);
  if (!isEmail(normalized)) throw new Error("Enter a valid email address");
  if (!isRole(role)) throw new Error("Unknown role");
  const id = randomUUID();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query(
      `INSERT INTO invites (id, org_id, email, role, token, expires_at, invited_by)
       VALUES ($1, $2, $3, $4, $5, $6, $7)`,
      [id, session.orgId, normalized, role, newInviteToken(), inviteExpiry(), session.userId],
    );
    await audit(client, session.orgId, session.userId, "invite.created", id);
  });
  // Mirror the invite into the environments registry as a membership, at
  // creation time rather than acceptance; syncInviteToEnvironments
  // documents why creation is the definitive point. No-op with the E0
  // adapter off, best-effort with it on.
  await syncInviteToEnvironments({
    orgId: session.orgId,
    actorEmail: session.email,
    email: normalized,
    role,
  });
  revalidateAdmin();
  return { id };
}

/** Admin-only: revoke a pending invite. */
export async function revokeInvite(inviteId: string): Promise<void> {
  const { session } = await requireAdmin();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      "UPDATE invites SET status = 'revoked' WHERE id = $1 AND org_id = $2 AND status = 'pending'",
      [inviteId, session.orgId],
    );
    if (result.rowCount === 0) throw new Error("That invite is no longer pending");
    await audit(client, session.orgId, session.userId, "invite.revoked", inviteId);
  });
  revalidateAdmin();
}

export type AcceptInviteResult =
  | { ok: true; orgId: string }
  | { ok: false; reason: Exclude<InviteVerdict, "ok"> | "wrong_email" };

/**
 * Accept an invite for the signed-in user. The token scopes invite
 * visibility (migration 0002); the membership insert rides the own-row
 * rule, so no org context is ever set from the client-presented token.
 */
export async function acceptInvite(token: string): Promise<AcceptInviteResult> {
  const session = await requireSession();
  if (!isInviteToken(token)) return { ok: false, reason: "invalid" };
  const result = await withUserContext(session.userId, async (client) => {
    await client.query("SELECT set_config('app.invite_token', $1, true)", [token]);
    const { rows } = await client.query<{
      id: string;
      orgId: string;
      email: string;
      role: string;
      status: string;
      expiresAt: Date;
    }>(
      `SELECT id, org_id AS "orgId", email, role, status, expires_at AS "expiresAt"
         FROM invites WHERE token = $1`,
      [token],
    );
    const invite = rows[0];
    if (!invite) return { ok: false as const, reason: "invalid" as const };
    const verdict = inviteVerdict(invite);
    if (verdict !== "ok") return { ok: false as const, reason: verdict };
    if (normalizeEmail(invite.email) !== normalizeEmail(session.email)) {
      return { ok: false as const, reason: "wrong_email" as const };
    }
    await client.query(
      `INSERT INTO memberships (org_id, user_id, role, status, invited_by)
       VALUES ($1, $2, $3, 'active', $4)
       ON CONFLICT (org_id, user_id) DO NOTHING`,
      [invite.orgId, session.userId, invite.role, null],
    );
    await client.query("UPDATE invites SET status = 'accepted' WHERE id = $1", [invite.id]);
    return { ok: true as const, orgId: invite.orgId, inviteId: invite.id };
  });
  if (!result.ok) return result;
  // Now a member: the audit row lands under the joined org's context.
  await withOrgContext({ orgId: result.orgId, userId: session.userId }, (client) =>
    audit(client, result.orgId, session.userId, "invite.accepted", result.inviteId),
  );
  await setActiveOrg(result.orgId);
  return { ok: true, orgId: result.orgId };
}

/** Admin-only: change a member's role. Admins cannot change their own. */
export async function updateMemberRole(userId: string, role: string): Promise<void> {
  const { session } = await requireAdmin();
  if (!isRole(role)) throw new Error("Unknown role");
  if (userId === session.userId) throw new Error("You cannot change your own role");
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      "UPDATE memberships SET role = $1 WHERE org_id = $2 AND user_id = $3",
      [role, session.orgId, userId],
    );
    if (result.rowCount === 0) throw new Error("That person is not a member");
    await audit(client, session.orgId, session.userId, "member.role_changed", `${userId} to ${role}`);
  });
  revalidateAdmin();
}

/** Admin-only: replace a member's capability grants. */
export async function updateMemberCapabilities(userId: string, capabilities: string[]): Promise<void> {
  const { session } = await requireAdmin();
  const parsed = parseCapabilities(capabilities);
  if (parsed === null) throw new Error("Unknown capability");
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      "UPDATE memberships SET capabilities = $1 WHERE org_id = $2 AND user_id = $3",
      [parsed, session.orgId, userId],
    );
    if (result.rowCount === 0) throw new Error("That person is not a member");
    await audit(
      client,
      session.orgId,
      session.userId,
      "member.capabilities_changed",
      `${userId} to [${parsed.join(", ")}]`,
    );
  });
  revalidateAdmin();
}

/** Admin-only: remove a member and their team seats. Not yourself. */
export async function removeMember(userId: string): Promise<void> {
  const { session } = await requireAdmin();
  if (userId === session.userId) throw new Error("You cannot remove yourself");
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query(
      `DELETE FROM team_memberships tm USING teams t
        WHERE t.id = tm.team_id AND t.org_id = $1 AND tm.user_id = $2`,
      [session.orgId, userId],
    );
    const result = await client.query("DELETE FROM memberships WHERE org_id = $1 AND user_id = $2", [
      session.orgId,
      userId,
    ]);
    if (result.rowCount === 0) throw new Error("That person is not a member");
    await audit(client, session.orgId, session.userId, "member.removed", userId);
  });
  revalidateAdmin();
}

/** Admin-only: create a team. */
export async function createTeam(name: string): Promise<{ id: string }> {
  const { session } = await requireAdmin();
  const trimmed = name.trim();
  if (trimmed.length < 2 || trimmed.length > 80) {
    throw new Error("Team name must be between 2 and 80 characters");
  }
  const id = randomUUID();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    await client.query("INSERT INTO teams (id, org_id, name) VALUES ($1, $2, $3)", [
      id,
      session.orgId,
      trimmed,
    ]);
    await audit(client, session.orgId, session.userId, "team.created", id);
  });
  revalidateAdmin();
  return { id };
}

/** Admin-only: delete a team; its seats cascade. */
export async function deleteTeam(teamId: string): Promise<void> {
  const { session } = await requireAdmin();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query("DELETE FROM teams WHERE id = $1 AND org_id = $2", [
      teamId,
      session.orgId,
    ]);
    if (result.rowCount === 0) throw new Error("That team does not exist");
    await audit(client, session.orgId, session.userId, "team.deleted", teamId);
  });
  revalidateAdmin();
}

/** Admin-only: seat an org member on a team. */
export async function addTeamMember(teamId: string, userId: string): Promise<void> {
  const { session } = await requireAdmin();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      `INSERT INTO team_memberships (team_id, user_id)
       SELECT $1, $2
        WHERE EXISTS (SELECT 1 FROM teams t WHERE t.id = $1 AND t.org_id = $3)
          AND EXISTS (
            SELECT 1 FROM memberships m
             WHERE m.org_id = $3 AND m.user_id = $2 AND m.status = 'active'
          )
       ON CONFLICT DO NOTHING`,
      [teamId, userId, session.orgId],
    );
    if (result.rowCount === 0) throw new Error("That person cannot be added to this team");
    await audit(client, session.orgId, session.userId, "team.member_added", `${userId} to ${teamId}`);
  });
  revalidateAdmin();
}

/** Admin-only: remove a member from a team. */
export async function removeTeamMember(teamId: string, userId: string): Promise<void> {
  const { session } = await requireAdmin();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    const result = await client.query(
      `DELETE FROM team_memberships tm USING teams t
        WHERE t.id = tm.team_id AND tm.team_id = $1 AND tm.user_id = $2 AND t.org_id = $3`,
      [teamId, userId, session.orgId],
    );
    if (result.rowCount === 0) throw new Error("That person is not on this team");
    await audit(
      client,
      session.orgId,
      session.userId,
      "team.member_removed",
      `${userId} from ${teamId}`,
    );
  });
  revalidateAdmin();
}
