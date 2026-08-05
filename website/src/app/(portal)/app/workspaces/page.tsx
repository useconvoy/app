import type { Metadata } from "next";

import { environmentsClient, routinesUsingWorkspace } from "@/lib/api/environments";
import { systemCatalog } from "@/lib/fixtures/environments";
import { routines } from "@/lib/fixtures/world";
import { createWorkspace } from "@/lib/workspaces/actions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { CreateWorkspaceModal } from "./CreateWorkspaceModal";
import { WorkspaceCards } from "./WorkspaceCards";

export const metadata: Metadata = { title: "Workspaces" };

/**
 * Workspaces list: Admin/Operator only, server-checked. Cards
 * carry the connects/used-by facts; the create modal hangs off the header.
 */
export default async function WorkspacesPage() {
  const { session } = await requireWorkspacesPage();
  const workspaces = await environmentsClient().listWorkspaces(session.orgId);
  const cards = workspaces.map((workspace) => ({
    id: workspace.id,
    name: workspace.name,
    purpose: workspace.purpose,
    systemCount: workspace.systems.length,
    routineCount: routinesUsingWorkspace(workspace, workspaces, routines).length,
  }));
  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="font-display text-3xl text-ink">Workspaces</h1>
          <p className="mt-2 text-sm text-muted">
            Where routines run: the systems they may touch and how far each grant goes.
          </p>
        </div>
        <CreateWorkspaceModal
          systems={systemCatalog.map((system) => ({
            id: system.id,
            displayName: system.displayName,
            sideEffecting: system.sideEffecting,
            standInNote: system.standInNote,
          }))}
          create={createWorkspace}
        />
      </header>
      <WorkspaceCards workspaces={cards} />
    </div>
  );
}
