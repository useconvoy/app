import type { Metadata } from "next";

import { Button } from "@/components/Button";
import { notificationClassLabels, notificationSettingsCopy } from "@/lexicon";
import { requireOrgSession } from "@/lib/auth/session";
import { saveNotificationPrefs } from "@/lib/notifications/actions";
import { listPrefs } from "@/lib/notifications/queries";
import { NOTIFICATION_CLASSES } from "@/notifier/rules";

export const metadata: Metadata = { title: "Notifications" };

/**
 * Per-class notification preferences. v1 channels: in-app only (D6); the
 * schema's channels[] keeps email/Slack a dispatcher away, so the other
 * channels render as "coming later" rather than pretending to exist. A
 * class without a stored row defaults to in-app on; unchecking writes an
 * explicit empty channels row the notifier's routing respects.
 */
export default async function NotificationSettingsPage() {
  const session = await requireOrgSession();
  const prefs = await listPrefs(session.orgId, session.userId);

  return (
    <div className="max-w-2xl space-y-6">
      <header>
        <h1 className="font-display text-2xl text-ink">{notificationSettingsCopy.title}</h1>
        <p className="mt-1 text-sm text-muted">{notificationSettingsCopy.intro}</p>
      </header>

      <form action={saveNotificationPrefs} className="rounded-md border border-line bg-card">
        <ul className="m-0 list-none divide-y divide-line-soft p-0">
          {NOTIFICATION_CLASSES.map((notificationClass) => {
            const channels = prefs[notificationClass];
            const inApp = channels ? channels.includes("in_app") : true;
            return (
              <li key={notificationClass} className="flex items-center justify-between gap-4 px-4 py-3">
                <div>
                  <p className="text-sm text-ink">{notificationClassLabels[notificationClass]}</p>
                  <p className="mt-0.5 text-xs text-muted">{notificationSettingsCopy.otherChannels}</p>
                </div>
                <label className="flex shrink-0 items-center gap-2 text-sm text-ink">
                  <input
                    type="checkbox"
                    name={notificationClass}
                    defaultChecked={inApp}
                    className="h-4 w-4 accent-pine"
                  />
                  {notificationSettingsCopy.inAppLabel}
                </label>
              </li>
            );
          })}
        </ul>
        <div className="border-t border-line-soft px-4 py-3">
          <Button type="submit">{notificationSettingsCopy.save}</Button>
        </div>
      </form>
    </div>
  );
}
