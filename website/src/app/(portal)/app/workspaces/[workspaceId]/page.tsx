import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { environmentsClient, routinesUsingWorkspace } from "@/lib/api/environments";
import { listRoutines } from "@/lib/routines/queries";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { WorkspaceDetail } from "./WorkspaceDetail";

export const metadata: Metadata = { title: "Workspace" };
export const dynamic = "force-dynamic";

/** Workspace detail: Admin/Operator only, server-checked. */
export default async function WorkspaceDetailPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  const { session } = await requireWorkspacesPage();
  const client = environmentsClient();
  const [workspace, workspaces, routines, executionEnvironments] = await Promise.all([
    client.getWorkspace(session.orgId, workspaceId),
    client.listWorkspaces(session.orgId),
    listRoutines(session.orgId),
    client.listExecutionEnvironments(session.orgId, workspaceId),
  ]);
  if (!workspace) notFound();
  const routineNames = routinesUsingWorkspace(workspace, workspaces, routines).map(
    (routine) => routine.name,
  );
  return (
    <WorkspaceDetail
      workspace={workspace}
      routineNames={routineNames}
      environments={executionEnvironments.map((environment) => ({
        id: environment.id,
        name: environment.name,
        isDefault: environment.isDefault,
      }))}
    />
  );
}
