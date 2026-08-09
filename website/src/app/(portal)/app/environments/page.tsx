import type { Metadata } from "next";

import { environmentsClient } from "@/lib/api/environments";
import { createExecutionEnvironment } from "@/lib/environments/actions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { CreateEnvironmentModal } from "./CreateEnvironmentModal";
import { EnvironmentCards } from "./EnvironmentCards";

export const metadata: Metadata = { title: "Environments" };
export const dynamic = "force-dynamic";

export default async function EnvironmentsPage() {
  const { session } = await requireWorkspacesPage();
  const client = environmentsClient();
  const [workspaces, environments] = await Promise.all([
    client.listWorkspaces(session.orgId),
    client.listExecutionEnvironments(session.orgId),
  ]);
  const workspaceNames = Object.fromEntries(
    workspaces.map((workspace) => [workspace.id, workspace.name]),
  );
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Environments</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted">
            Runtime configurations beneath a workspace: compute, browser access, and durable state.
          </p>
        </div>
        <CreateEnvironmentModal
          workspaces={workspaces.map((workspace) => ({
            id: workspace.id,
            name: workspace.name,
            systemCount: workspace.systems.length,
          }))}
          create={createExecutionEnvironment}
        />
      </header>
      {workspaces.length === 0 && (
        <p role="status" className="rounded-md border border-hold-soft bg-hold-soft p-3 text-sm text-hold-text">
          Create a workspace and connect its systems before creating an environment.
        </p>
      )}
      <EnvironmentCards environments={environments} workspaceNames={workspaceNames} />
    </div>
  );
}
