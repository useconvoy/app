import type { Metadata } from "next";

import { environmentsClient, routinesUsingWorkspace } from "@/lib/api/environments";
import { listRoutines } from "@/lib/routines/queries";
import { createWorkspace } from "@/lib/workspaces/actions";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { systemCatalog } from "@/lib/workspaces/system-catalog";
import { CreateWorkspaceModal } from "./CreateWorkspaceModal";
import { WorkspaceCards } from "./WorkspaceCards";

export const metadata: Metadata = { title: "Workspaces" };
export const dynamic = "force-dynamic";

/**
 * Workspaces list: Admin/Operator only, server-checked. Cards
 * carry the connects/used-by facts; the create modal hangs off the header,
 * and a fresh organization sees an honest empty state under it.
 */
export default async function WorkspacesPage() {
  const { session } = await requireWorkspacesPage();
  const [workspaces, routines] = await Promise.all([
    environmentsClient().listWorkspaces(session.orgId),
    listRoutines(session.orgId),
  ]);
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
