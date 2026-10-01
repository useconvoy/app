"use client";

import Link from "next/link";
import { currentRevision, getConfiguration, nextRevision, useWorkspace } from "@/lib/configurations/client";
import { QUERY, routes } from "@/lib/configurations/routes";
import { AppShell, type Crumb } from "../AppShell";
import { Badge } from "../Badges";
import { CompatList } from "../Compat";
import { useQueryState } from "../hooks";
import { WorkspaceNotice, WorkspaceSourceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { LoadingState, PagePlaceholder } from "../States";

const CRUMBS: Crumb[] = [{ label: "Configurations", href: routes.index() }, { label: "New configuration" }];

/** Screen 1b · New configuration (`/app/configurations/new?from=<configId>`). Page body: work package 1. */
export function NewConfigurationPage() {
  const ws = useWorkspace();
  const [from] = useQueryState(QUERY.from);
  if (ws.status === "loading") return <AppShell crumbs={CRUMBS}><LoadingState /></AppShell>;
  const workspace = ws.workspace;
  const base = from ? getConfiguration(workspace, from) : null;
  const revision = base ? currentRevision(base) : null;
  const nextRev = base ? nextRevision(base) : "r1";
  return <AppShell crumbs={CRUMBS}>
    <PageHeader eyebrow="New configuration" title={base ? `${base.name} ${nextRev}` : "New configuration"} badges={<Badge>Draft</Badge>}
      actions={<Link className="cfg-btn-text" href={base ? routes.configuration(base.id) : routes.index()}>Cancel</Link>}
      meta={`${base && revision ? `Started from ${revision.rev} · ` : ""}Not saved yet · Nothing deploys until you create it`} />
    <WorkspaceSourceNotice />
    <WorkspaceNotice workspace={workspace} />
    <PagePlaceholder title="New configuration" items={[
      "Stepper: 01 Robot · 02 Edge hardware · 03 Edge model · 04 Cloud model · 05 Routing & safety · 06 Review",
      "Review: summary panels with Edit links and the compatibility check",
      "Create configuration (createConfiguration + save)",
    ]} />
    {revision && <section className="portal-panel cfg-section" aria-labelledby="new-compat-title">
      <div className="portal-panel-heading"><div><p className="portal-eyebrow">Starting point · {revision.rev}</p><h2 id="new-compat-title">Compatibility check</h2></div></div>
      <CompatList checks={revision.compatibility} />
    </section>}
  </AppShell>;
}
