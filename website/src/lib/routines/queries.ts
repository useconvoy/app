/**
 * Server-only data access for routine assignments (website-side routing
 * facts, DESIGN §3). Every query runs through withOrgContext so RLS bounds
 * it; org ids arrive from the verified session, never from client input.
 */
import "server-only";

import { withOrgContext } from "@/lib/db";

export interface ApproverAssignment {
  id: string;
  assigneeType: "user" | "team";
  assigneeId: string;
  /** Display name of the person or team, for assignment chips. */
  name: string;
}

/** Approvers assigned to a routine, people and teams together. */
export async function listApprovers(orgId: string, routineId: string): Promise<ApproverAssignment[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<ApproverAssignment>(
      `SELECT ra.id, ra.assignee_type AS "assigneeType", ra.assignee_id AS "assigneeId",
              COALESCE(u.name, t.name, 'Removed') AS name
         FROM routine_assignments ra
         LEFT JOIN users u ON ra.assignee_type = 'user' AND u.id = ra.assignee_id
         LEFT JOIN teams t ON ra.assignee_type = 'team' AND t.id = ra.assignee_id
        WHERE ra.org_id = $1 AND ra.routine_id = $2 AND ra.relationship = 'approver'
        ORDER BY name`,
      [orgId, routineId],
    );
    return rows;
  });
}

/**
 * Whether a user is assigned to a routine, directly or through a team, in
 * any relationship. D13's gate: assigned Members may trigger on-demand
 * production runs within caps.
 */
export async function isAssignedToRoutine(
  orgId: string,
  userId: string,
  routineId: string,
): Promise<boolean> {
  return withOrgContext({ orgId, userId }, async (client) => {
    const { rows } = await client.query<{ assigned: boolean }>(
      `SELECT EXISTS (
         SELECT 1 FROM routine_assignments ra
          WHERE ra.org_id = $1 AND ra.routine_id = $2
            AND (
              (ra.assignee_type = 'user' AND ra.assignee_id = $3)
              OR (ra.assignee_type = 'team' AND ra.assignee_id IN (
                    SELECT tm.team_id FROM team_memberships tm WHERE tm.user_id = $3))
            )
       ) AS assigned`,
      [orgId, routineId, userId],
    );
    return rows[0]?.assigned ?? false;
  });
}

/** The runtime tenant id for an org, for the actor context on run calls. */
export async function orgTenantId(orgId: string): Promise<string> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<{ tenantId: string }>(
      `SELECT tenant_id AS "tenantId" FROM organizations WHERE id = $1`,
      [orgId],
    );
    const tenantId = rows[0]?.tenantId;
    if (!tenantId) throw new Error("organization not found");
    return tenantId;
  });
}
