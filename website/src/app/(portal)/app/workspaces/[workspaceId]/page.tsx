import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { environmentsClient } from "@/lib/api/environments";
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
  const [workspace, agents] = await Promise.all([
    client.getWorkspace(session.orgId, workspaceId),
    client.listAgents(session.orgId, workspaceId),
  ]);
  if (!workspace) notFound();
  return (
    <WorkspaceDetail
      workspace={workspace}
      agents={agents.map((agent) => ({
        id: agent.id,
        name: agent.name,
      }))}
    />
  );
}
