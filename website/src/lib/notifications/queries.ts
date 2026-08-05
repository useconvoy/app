/**
 * Read/mark access to the signed-in user's notification inbox and prefs.
 * The notifier is the only writer of notifications; this
 * module only reads them and flips read_at on the user's own rows. RLS
 * already scopes notifications to (active org, own user); the queries
 * repeat the predicates for clarity and index use.
 */
import "server-only";

import { withOrgContext } from "@/lib/db";

export interface NotificationRow {
  id: string;
  notificationClass: string;
  title: string;
  ctaUrl: string;
  runId: string;
  createdAt: Date;
  readAt: Date | null;
}

export interface Inbox {
  unread: NotificationRow[];
  recent: NotificationRow[];
}

const ROW_SELECT = `SELECT id, class AS "notificationClass", title, cta_url AS "ctaUrl",
       run_id AS "runId", created_at AS "createdAt", read_at AS "readAt"
  FROM notifications
 WHERE org_id = $1 AND user_id = $2`;

/** The bell panel's data: everything unread plus a few recent read items. */
export async function listInbox(orgId: string, userId: string): Promise<Inbox> {
  return withOrgContext({ orgId, userId }, async (client) => {
    const unread = await client.query<NotificationRow>(
      `${ROW_SELECT} AND read_at IS NULL ORDER BY created_at DESC LIMIT 50`,
      [orgId, userId],
    );
    const recent = await client.query<NotificationRow>(
      `${ROW_SELECT} AND read_at IS NOT NULL ORDER BY created_at DESC LIMIT 10`,
      [orgId, userId],
    );
    return { unread: unread.rows, recent: recent.rows };
  });
}

/** Mark the user's own notifications read; returns how many flipped. */
export async function markNotificationsRead(
  orgId: string,
  userId: string,
  ids: string[],
): Promise<number> {
  if (ids.length === 0) return 0;
  return withOrgContext({ orgId, userId }, async (client) => {
    const result = await client.query(
      `UPDATE notifications SET read_at = now()
        WHERE org_id = $1 AND user_id = $2 AND id = ANY($3) AND read_at IS NULL`,
      [orgId, userId, ids],
    );
    return result.rowCount ?? 0;
  });
}

/** The user's per-class channel choices; classes without a row use defaults. */
export async function listPrefs(orgId: string, userId: string): Promise<Record<string, string[]>> {
  return withOrgContext({ orgId, userId }, async (client) => {
    const { rows } = await client.query<{ notificationClass: string; channels: string[] }>(
      `SELECT class AS "notificationClass", channels
         FROM notification_prefs WHERE org_id = $1 AND user_id = $2`,
      [orgId, userId],
    );
    return Object.fromEntries(rows.map((row) => [row.notificationClass, row.channels]));
  });
}
