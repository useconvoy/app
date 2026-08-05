/**
 * Server action for the notification settings page. Prefs are per user,
 * per org, per class; v1 exposes the in_app channel only (D6) while the
 * channels[] schema stays ready for email/Slack dispatchers later. Org and
 * user always derive from the verified session, never from the form.
 */
"use server";

import { revalidatePath } from "next/cache";

import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { NOTIFICATION_CLASSES } from "@/notifier/rules";

/**
 * Persist one row per class: checked means channels = {in_app}, unchecked
 * means an empty channels array (an explicit opt-out the notifier's
 * routing respects). Unknown form fields are ignored; only the known
 * classes are ever written.
 */
export async function saveNotificationPrefs(formData: FormData): Promise<void> {
  const session = await requireOrgSession();
  await withOrgContext({ orgId: session.orgId, userId: session.userId }, async (client) => {
    for (const notificationClass of NOTIFICATION_CLASSES) {
      const enabled = formData.get(notificationClass) === "on";
      await client.query(
        `INSERT INTO notification_prefs (user_id, org_id, class, channels)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (user_id, org_id, class) DO UPDATE SET channels = EXCLUDED.channels`,
        [session.userId, session.orgId, notificationClass, enabled ? ["in_app"] : []],
      );
    }
  });
  revalidatePath("/app/settings/notifications");
}
