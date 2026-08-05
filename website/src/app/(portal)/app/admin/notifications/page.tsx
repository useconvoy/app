import type { Metadata } from "next";
import Link from "next/link";

import { Button } from "@/components/Button";
import { requireAdminPage } from "@/lib/orgs/admin-gate";
import { getOrgSettings } from "@/lib/orgs/settings";
import { updateNotificationDefaults } from "@/lib/orgs/settings-actions";
import { NOTIFICATION_CLASSES } from "@/notifier/rules";
import { adminCopy, notificationClassLabels, notificationSettingsCopy } from "@/lexicon";

export const metadata: Metadata = { title: "Notification defaults" };

/**
 * Org notification defaults, stored in
 * organizations.settings.notification_defaults. The notifier reads a
 * person's own preferences first and falls back to these (see
 * notifier/routing). v1 channels are in-app only: in app renders
 * fixed on, email and Slack as coming later; saving records the explicit
 * in-app default per class.
 */
export default async function NotificationDefaultsPage() {
  const { session } = await requireAdminPage();
  const settings = await getOrgSettings(session.orgId);
  const savedClasses = Object.keys(settings.notificationDefaults).length;
  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href="/app/admin" className="underline">
            Admin
          </Link>
        </p>
        <h1 className="mt-1 font-display text-2xl text-ink">{adminCopy.notificationDefaultsTitle}</h1>
        <p className="mt-1 text-sm text-muted">{adminCopy.notificationDefaultsIntro}</p>
      </header>

      <form action={updateNotificationDefaults} className="rounded-md border border-line bg-card">
        <ul className="m-0 list-none divide-y divide-line-soft p-0">
          {NOTIFICATION_CLASSES.map((notificationClass) => (
            <li key={notificationClass} className="flex items-center justify-between gap-4 px-4 py-3">
              <div>
                <p className="text-sm text-ink">{notificationClassLabels[notificationClass]}</p>
                <p className="mt-0.5 text-xs text-muted">{notificationSettingsCopy.otherChannels}</p>
              </div>
              <label className="flex shrink-0 items-center gap-2 text-sm text-muted">
                <input type="checkbox" checked disabled readOnly className="h-4 w-4 accent-pine" />
                {adminCopy.inAppFixedOn}
              </label>
            </li>
          ))}
        </ul>
        <div className="flex items-center gap-3 border-t border-line-soft px-4 py-3">
          <Button type="submit">{adminCopy.saveDefaults}</Button>
          {savedClasses > 0 ? (
            <p className="font-mono text-xs uppercase text-muted">
              {savedClasses} {savedClasses === 1 ? "class" : "classes"} saved
            </p>
          ) : null}
        </div>
      </form>
    </div>
  );
}
