import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { environmentsClient, routinesUsingWorkspace } from "@/lib/api/environments";
import { routines } from "@/lib/fixtures/world";
import { requireWorkspacesPage } from "@/lib/workspaces/gate";
import { WorkspaceDetail } from "./WorkspaceDetail";

export const metadata: Metadata = { title: "Workspace" };

/** Workspace detail: Admin/Operator only, server-checked. */
export default async function WorkspaceDetailPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>;
}) {
  const { workspaceId } = await params;
  const { session } = await requireWorkspacesPage();
  const client = environmentsClient();
  const [workspace, workspaces] = await Promise.all([
    client.getWorkspace(session.orgId, workspaceId),
    client.listWorkspaces(session.orgId),
  ]);
  if (!workspace) notFound();
  const routineNames = routinesUsingWorkspace(workspace, workspaces, routines).map(
    (routine) => routine.name,
  );
  return <WorkspaceDetail workspace={workspace} routineNames={routineNames} />;
}
