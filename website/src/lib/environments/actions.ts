"use server";

import { revalidatePath } from "next/cache";

import { environmentsClient } from "@/lib/api/environments";
import { requireOrgSession } from "@/lib/auth/session";
import { withOrgContext } from "@/lib/db";
import { getMembership } from "@/lib/orgs/queries";
import { can } from "@/lib/permissions";

export interface CreateExecutionEnvironmentPayload {
  workspaceId: string;
  name: string;
  purpose: string;
  sandboxTemplate: string;
  browserEnabled: boolean;
  allowedDomains: string[];
  persistBrowserProfile: boolean;
  makeDefault: boolean;
}

export async function createExecutionEnvironment(
  payload: CreateExecutionEnvironmentPayload,
): Promise<{ id: string }> {
  const session = await requireOrgSession();
  const membership = await getMembership(session.orgId, session.userId);
  if (
    !membership ||
    membership.status !== "active" ||
    !can("manage_workspaces", membership.role, membership.capabilities)
  ) {
    throw new Error("You cannot manage environments");
  }

  const name = payload.name.trim();
  const purpose = payload.purpose.trim();
  const sandboxTemplate = payload.sandboxTemplate.trim();
  if (name.length < 2 || name.length > 120) {
    throw new Error("Environment name must be between 2 and 120 characters");
  }
  if (purpose.length < 2 || purpose.length > 200) {
    throw new Error("Describe what runs in this environment");
  }
  if (sandboxTemplate.length < 2 || sandboxTemplate.length > 128) {
    throw new Error("Choose a valid compute template");
  }
  const workspace = await environmentsClient().getWorkspace(session.orgId, payload.workspaceId);
  if (!workspace) throw new Error("That workspace does not exist");

  const allowedDomains = [...new Set(payload.allowedDomains.map((domain) => domain.trim().toLowerCase()))]
    .filter(Boolean);
  if (allowedDomains.some((domain) => !/^(\*\.)?[a-z0-9][a-z0-9.-]*[a-z0-9]$/i.test(domain))) {
    throw new Error("Browser domains must be hostnames such as app.example.com");
  }

  const environment = await environmentsClient().createExecutionEnvironment(session.orgId, {
    workspaceId: workspace.id,
    name,
    purpose,
    sandboxTemplate,
    browserPolicy: payload.browserEnabled
      ? { allowedDomains, persistProfile: payload.persistBrowserProfile }
      : null,
    makeDefault: payload.makeDefault,
  });

  await withOrgContext({ orgId: session.orgId, userId: session.userId }, (client) =>
    client
      .query("INSERT INTO admin_audit (org_id, actor_id, action, subject) VALUES ($1, $2, $3, $4)", [
        session.orgId,
        session.userId,
        "environment.created",
        environment.id,
      ])
      .then(() => undefined),
  );
  revalidatePath("/app/environments");
  revalidatePath(`/app/workspaces/${workspace.id}`);
  return { id: environment.id };
}
