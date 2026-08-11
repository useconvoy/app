/**
 * Who gets notified. Pure resolution over an
 * in-memory `RoutingWorld` so the matching rules are unit-testable; a small
 * loader hydrates that world from the website DB under the org's RLS
 * context.
 *
 * Routing rules:
 * - checkpoint classes (checkpoint_opened, checkpoint_deadline) go to the
 *   routine's approvers and owners;
 * - budget_warning, budget_exhausted, run_failed, run_landed, and
 *   improvement_ready go to owners and watchers;
 * - promotion_requested goes to members holding the promoter capability
 *   (Operators hold it implicitly, per the permissions matrix).
 * - a run whose routine is unknown or has no assignment rows at all falls
 *   back to org admins and operators: someone accountable always hears
 *   about a stuck or broken run. A routine that has assignments but none in
 *   the target relationships resolves to nobody by design; its people chose
 *   their relationships.
 *
 * Teams expand to every member (a team-held item notifies all members;
 * first response wins is an answering concern handled by the runtime's
 * gate_answered actor, not a notification concern). Each person receives at
 * most one notification per event regardless of how many routes hit them.
 * notification_prefs filter last: a class row without "in_app" suppresses;
 * without a row, the org's stored default channels for the class apply
 * (organizations.settings.notification_defaults), and
 * with neither, in-app stays on. Personal choices always win.
 */
import type { PoolClient } from "pg";

import type { NotificationClass } from "./rules";

export type Relationship = "owner" | "approver" | "watcher";

export interface AssignmentRow {
  routineId: string;
  assigneeType: "user" | "team";
  assigneeId: string;
  relationship: Relationship;
}

export interface MemberRow {
  userId: string;
  role: string;
  capabilities: string[];
  status: string;
}

export interface PrefRow {
  userId: string;
  notificationClass: string;
  channels: string[];
}

export interface RoutingWorld {
  assignments: AssignmentRow[];
  /** team id -> member user ids */
  teamMembers: Map<string, string[]>;
  members: MemberRow[];
  prefs: PrefRow[];
  /**
   * Org default channels for the class being routed, from
   * organizations.settings.notification_defaults; absent or null means no
   * default is stored and in-app stays on.
   */
  defaultChannels?: string[] | null;
}

const CLASS_RELATIONSHIPS: Record<NotificationClass, Relationship[] | "promoters"> = {
  checkpoint_opened: ["approver", "owner"],
  checkpoint_deadline: ["approver", "owner"],
  promotion_requested: "promoters",
  budget_warning: ["owner", "watcher"],
  budget_exhausted: ["owner", "watcher"],
  run_failed: ["owner", "watcher"],
  run_landed: ["owner", "watcher"],
  improvement_ready: ["owner", "watcher"],
};

function activeMembers(world: RoutingWorld): Map<string, MemberRow> {
  const map = new Map<string, MemberRow>();
  for (const member of world.members) {
    if (member.status === "active") map.set(member.userId, member);
  }
  return map;
}

function inAppEnabled(world: RoutingWorld, userId: string, cls: NotificationClass): boolean {
  const pref = world.prefs.find(
    (row) => row.userId === userId && row.notificationClass === cls,
  );
  if (pref) return pref.channels.includes("in_app");
  // No personal row: the org's stored default for the class decides;
  // no stored default keeps in-app on.
  if (world.defaultChannels) return world.defaultChannels.includes("in_app");
  return true;
}

/**
 * Resolve the user ids to notify for a class, deduplicated, active members
 * only, prefs applied. `routineId` is undefined when the run cannot be tied
 * to a routine.
 */
export function resolveRecipients(
  world: RoutingWorld,
  cls: NotificationClass,
  routineId: string | undefined,
): string[] {
  const active = activeMembers(world);
  const targets = CLASS_RELATIONSHIPS[cls];
  const ordered: string[] = [];
  const push = (userId: string) => {
    if (active.has(userId) && !ordered.includes(userId)) ordered.push(userId);
  };

  if (targets === "promoters") {
    for (const member of active.values()) {
      if (member.role === "operator" || member.capabilities.includes("promoter")) {
        push(member.userId);
      }
    }
  } else {
    const routineRows = routineId
      ? world.assignments.filter((row) => row.routineId === routineId)
      : [];
    if (routineRows.length === 0) {
      // No routine or no assignments at all: admins + operators (see docstring).
      for (const member of active.values()) {
        if (member.role === "admin" || member.role === "operator") push(member.userId);
      }
    } else {
      for (const relationship of targets) {
        for (const row of routineRows) {
          if (row.relationship !== relationship) continue;
          if (row.assigneeType === "user") {
            push(row.assigneeId);
          } else {
            for (const userId of world.teamMembers.get(row.assigneeId) ?? []) push(userId);
          }
        }
      }
    }
  }

  return ordered.filter((userId) => inAppEnabled(world, userId, cls));
}

/**
 * Hydrate the routing world for one org, routine, and class from the
 * website DB. Runs on a client already inside withOrgContext, so RLS bounds
 * every read to the org being processed (prefs ride the 0003 org-read
 * policy).
 */
export async function loadRoutingWorld(
  client: PoolClient,
  orgId: string,
  routineId: string | undefined,
  cls: NotificationClass,
): Promise<RoutingWorld> {
  const assignments: AssignmentRow[] = routineId
    ? (
        await client.query<AssignmentRow>(
          `SELECT agent_id AS "routineId", assignee_type AS "assigneeType",
                  assignee_id AS "assigneeId", relationship
             FROM agent_assignments
            WHERE org_id = $1 AND agent_id = $2`,
          [orgId, routineId],
        )
      ).rows
    : [];

  const teamIds = [
    ...new Set(
      assignments.filter((row) => row.assigneeType === "team").map((row) => row.assigneeId),
    ),
  ];
  const teamMembers = new Map<string, string[]>();
  if (teamIds.length > 0) {
    const { rows } = await client.query<{ teamId: string; userId: string }>(
      `SELECT team_id AS "teamId", user_id AS "userId"
         FROM team_memberships WHERE team_id = ANY($1)`,
      [teamIds],
    );
    for (const row of rows) {
      const seats = teamMembers.get(row.teamId) ?? [];
      seats.push(row.userId);
      teamMembers.set(row.teamId, seats);
    }
  }

  const members = (
    await client.query<MemberRow>(
      `SELECT user_id AS "userId", role, capabilities, status
         FROM memberships WHERE org_id = $1`,
      [orgId],
    )
  ).rows;

  const prefs = (
    await client.query<PrefRow>(
      `SELECT user_id AS "userId", class AS "notificationClass", channels
         FROM notification_prefs WHERE org_id = $1 AND class = $2`,
      [orgId, cls],
    )
  ).rows;

  // Org default channels for the class: the fallback when a person
  // has no prefs row. The organizations row is visible to the notifier's
  // org context through the id = app_org_id() select policy.
  const { rows: defaultsRows } = await client.query<{ channels: unknown }>(
    `SELECT settings -> 'notification_defaults' -> $2 AS channels
       FROM organizations WHERE id = $1`,
    [orgId, cls],
  );
  const storedDefault = defaultsRows[0]?.channels;
  const defaultChannels =
    Array.isArray(storedDefault) && storedDefault.every((channel) => typeof channel === "string")
      ? (storedDefault as string[])
      : null;

  return { assignments, teamMembers, members, prefs, defaultChannels };
}
