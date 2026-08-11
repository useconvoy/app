import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { EmptyState } from "@/components/EmptyState";
import { environmentsClient } from "@/lib/api/environments";
import { installEntry } from "@/lib/catalog/actions";
import { computeCompatibility } from "@/lib/catalog/compat";
import { requireCatalogPage } from "@/lib/catalog/gate";
import { getEntry } from "@/lib/catalog/queries";
import { systemCatalog } from "@/lib/workspaces/system-catalog";
import { catalogCopy, copy } from "@/lexicon";
import { InstallFlow } from "./InstallFlow";

export const metadata: Metadata = { title: "Install an Agent template" };

/**
 * The install screen: the server resolves the Agent template's
 * capability requirements against every workspace up front, so picking a
 * workspace shows its compatibility report instantly; confirming installs
 * with the snapshot version pinned.
 */
export default async function InstallPage({ params }: { params: Promise<{ entryId: string }> }) {
  const { entryId } = await params;
  const { session } = await requireCatalogPage();
  const entry = await getEntry({ orgId: session.orgId, userId: session.userId }, entryId);
  if (!entry) notFound();

  const client = environmentsClient();
  const [workspaces, agents] = await Promise.all([
    client.listWorkspaces(session.orgId),
    client.listAgents(session.orgId),
  ]);
  const workspacesById = new Map(workspaces.map((workspace) => [workspace.id, workspace]));
  const options = agents.flatMap((agent) => {
    const workspace = workspacesById.get(agent.workspaceId);
    return workspace
      ? [{
          agentId: agent.id,
          agentName: agent.name,
          workspaceId: workspace.id,
          workspaceName: workspace.name,
          report: computeCompatibility(entry.requirements, workspace),
        }]
      : [];
  });

  // Plain names for every id the reports may mention: the shared system
  // catalog plus whatever concrete systems the workspaces connect.
  const systemNames: Record<string, string> = {};
  for (const system of systemCatalog) systemNames[system.id] = system.displayName;
  for (const workspace of workspaces) {
    for (const grant of workspace.systems) systemNames[grant.systemId] = grant.displayName;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header>
        <p className="text-xs text-muted">
          <Link href={`/app/catalog/${entry.id}`} className="underline">
            {entry.storefront.name}
          </Link>
        </p>
        <h1 className="mt-1 font-display text-3xl text-ink">{catalogCopy.installRoutine}</h1>
        <p className="mt-2 text-sm text-muted">{entry.storefront.tagline}</p>
      </header>
      {options.length > 0 ? (
        <InstallFlow
          entryId={entry.id}
          version={entry.version}
          options={options}
          systemNames={systemNames}
          install={installEntry}
        />
      ) : (
        <EmptyState
          title={agents.length === 0 ? "Create an agent first" : copy.workspacesEmptyTitle}
          body={agents.length === 0
            ? "An Agent template needs an Agent already linked to a shared Workspace and runtime setup."
            : catalogCopy.installNeedsWorkspace}
          action={
            <Link
              href={agents.length === 0 ? "/app/agents" : "/app/workspaces"}
              className="rounded-md border border-line bg-card px-3 py-1.5 text-sm font-medium text-ink hover:border-pine"
            >
              {agents.length === 0 ? "Go to agents" : catalogCopy.goToWorkspaces}
            </Link>
          }
        />
      )}
    </div>
  );
}
