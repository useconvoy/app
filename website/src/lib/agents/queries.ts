/**
 * Agent-owned product configuration and assignment reads. A Workspace is a
 * shared integration boundary: many Agents may reference the same Workspace.
 */
import "server-only";

import { withOrgContext } from "@/lib/db";

export interface ApproverAssignment {
  id: string;
  assigneeType: "user" | "team";
  assigneeId: string;
  name: string;
}
export async function listAgentApprovers(
  orgId: string,
  agentId: string,
): Promise<ApproverAssignment[]> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<ApproverAssignment>(
      `SELECT aa.id, aa.assignee_type AS "assigneeType", aa.assignee_id AS "assigneeId",
              COALESCE(u.name, t.name, 'Removed') AS name
         FROM agent_assignments aa
         LEFT JOIN users u ON aa.assignee_type = 'user' AND u.id = aa.assignee_id
         LEFT JOIN teams t ON aa.assignee_type = 'team' AND t.id = aa.assignee_id
        WHERE aa.org_id = $1 AND aa.agent_id = $2 AND aa.relationship = 'approver'
        ORDER BY name`,
      [orgId, agentId],
    );
    return rows;
  });
}

export async function isAssignedToAgent(
  orgId: string,
  userId: string,
  agentId: string,
): Promise<boolean> {
  return withOrgContext({ orgId, userId }, async (client) => {
    const { rows } = await client.query<{ assigned: boolean }>(
      `SELECT EXISTS (
         SELECT 1 FROM agent_assignments aa
          WHERE aa.org_id = $1 AND aa.agent_id = $2
            AND (
              (aa.assignee_type = 'user' AND aa.assignee_id = $3)
              OR (aa.assignee_type = 'team' AND aa.assignee_id IN (
                    SELECT tm.team_id FROM team_memberships tm WHERE tm.user_id = $3))
            )
       ) AS assigned`,
      [orgId, agentId, userId],
    );
    return rows[0]?.assigned ?? false;
  });
}

export async function orgTenantId(orgId: string): Promise<string> {
  return withOrgContext({ orgId }, async (client) => {
    const { rows } = await client.query<{ tenantId: string }>(
      `SELECT COALESCE(environments_org_id, tenant_id) AS "tenantId"
         FROM organizations WHERE id = $1`,
      [orgId],
    );
    const tenantId = rows[0]?.tenantId;
    if (!tenantId) throw new Error("organization not found");
    return tenantId;
  });
}
