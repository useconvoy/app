/**
 * Server actions for org policies and notification defaults.
 * Org and actor derive from the verified session, the
 * manage_org_settings cell of the permissions matrix is re-checked
 * server-side, shapes are zod-validated, and the cores write admin_audit
 * rows transactionally.
 */
"use server";

import { revalidatePath } from "next/cache";

import { requireOrgSession } from "@/lib/auth/session";
import { can } from "@/lib/permissions";
import { NOTIFICATION_CLASSES } from "@/notifier/rules";
import { getMembership } from "./queries";
import {
  policiesSchema,
  saveNotificationDefaultsCore,
  savePoliciesCore,
  type NotificationDefaults,
  type OrgPolicies,
} from "./settings";

async function requireOrgSettingsAdmin() {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_org_settings", membership.role, membership.capabilities)
  ) {
    throw new Error("Only organization admins can change org settings");
  }
  return { session, membership };
}

export async function updateOrgPolicies(policies: OrgPolicies): Promise<void> {
  const { session } = await requireOrgSettingsAdmin();
  const parsed = policiesSchema.safeParse(policies);
  if (!parsed.success) throw new Error("Check the policy amounts and try again");
  await savePoliciesCore({ orgId: session.orgId, userId: session.userId }, parsed.data);
  revalidatePath("/app/admin/policies");
  revalidatePath("/app/admin");
}

/**
 * Save the org notification defaults. v1 keeps in_app fixed on for every
 * class (v1 channels are in-app only), so the action normalizes whatever arrives to
 * that shape; the schema stays wider so email/Slack are a dispatcher away.
 */
export async function updateNotificationDefaults(): Promise<void> {
  const { session } = await requireOrgSettingsAdmin();
  const defaults: NotificationDefaults = {};
  for (const cls of NOTIFICATION_CLASSES) defaults[cls] = ["in_app"];
  await saveNotificationDefaultsCore({ orgId: session.orgId, userId: session.userId }, defaults);
  revalidatePath("/app/admin/notifications");
}
