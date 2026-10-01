"use client";

import Link from "next/link";
import { configurationSummary, listConfigurations, useWorkspace } from "@/lib/configurations/client";
import { fmtUpdated } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { AppShell, type Crumb } from "../AppShell";
import { ConfigStatusBadges } from "../Badges";
import { Icon } from "../Icons";
import { WorkspaceNotice, WorkspaceSourceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { LoadingState, PagePlaceholder } from "../States";

const CRUMBS: Crumb[] = [{ label: "Configurations" }];

/** Screen 1 · Configurations index (`/app/configurations`). Page body: work package 1. */
export function ConfigurationsIndexPage() {
  const ws = useWorkspace();
  if (ws.status === "loading") return <AppShell crumbs={CRUMBS}><LoadingState /></AppShell>;
  const workspace = ws.workspace;
  const configurations = listConfigurations(workspace);
  const attached = workspace.robots.filter(robot => robot.configId !== null).length;
  return <AppShell crumbs={CRUMBS}>
    <PageHeader eyebrow="Configurations" title="Configurations"
      actions={<Link className="btn btn-primary cfg-btn" href={routes.newConfiguration()}>Add configuration <Icon name="plus" /></Link>}
      meta={`${configurations.length} configurations · ${attached} robots attached · ${fmtUpdated(workspace.meta.updatedAt, null)}`} />
    <WorkspaceSourceNotice />
    <WorkspaceNotice workspace={workspace} />
    <PagePlaceholder title="Configurations index" items={[
      "Search, status chips (All · Testing · In production · Draft) and sort",
      "Configuration cards with stack, counts, attention and latest eval, plus the add tile",
      "Recent activity",
    ]} />
    <ul className="cfg-placeholder-list" aria-label="Configurations">
      {configurations.map(configuration => {
        const summary = configurationSummary(workspace, configuration);
        return <li key={configuration.id}>
          <Link className="portal-table-link" href={routes.configuration(configuration.id)}>{configuration.name}</Link>
          <span className="cfg-badges"><ConfigStatusBadges configuration={configuration} /></span>
          <span>Test {summary.robots.test} · Production {summary.robots.production}</span>
        </li>;
      })}
    </ul>
  </AppShell>;
}
