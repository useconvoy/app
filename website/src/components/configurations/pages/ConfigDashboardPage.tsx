"use client";

import Link from "next/link";
import { flaggedRobots, getConfiguration, getRevision, robotHref, robotReadings, robotsFor, useWorkspace } from "@/lib/configurations/client";
import { fmtUpdated } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { AppShell, type Crumb } from "../AppShell";
import { ConfigStatusBadges, HealthBadge, ProvenanceBadge, RoleBadge } from "../Badges";
import { useNow } from "../hooks";
import { useLiveRobots } from "../LiveDeviceProvider";
import { Notice, WorkspaceNotice, WorkspaceSourceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { LoadingState, NotFoundState, PagePlaceholder } from "../States";

/** Screen 2 · Configuration dashboard (`/app/configurations/[configId]`, `?tab=`). Page body: work package 2. */
export function ConfigDashboardPage({ configId }: { configId: string }) {
  const ws = useWorkspace();
  const now = useNow();
  const configuration = ws.workspace ? getConfiguration(ws.workspace, configId) : null;
  const robots = ws.workspace && configuration ? robotsFor(ws.workspace, configuration.id) : [];
  const live = useLiveRobots(robots);
  const root: Crumb = { label: "Configurations", href: routes.index() };
  if (ws.status === "loading") return <AppShell crumbs={[root, { label: "Configuration" }]}><LoadingState /></AppShell>;
  if (!configuration) {
    return <AppShell crumbs={[root, { label: configId }]}>
      <NotFoundState title="This configuration is not in the workspace." text={`No configuration has the id “${configId}”. It may have been removed or renamed.`} href={routes.index()} linkLabel="Back to configurations" />
    </AppShell>;
  }
  const workspace = ws.workspace;
  const flagged = flaggedRobots(workspace, configuration.id, live, now);
  return <AppShell crumbs={[root, { label: configuration.name }]}>
    <PageHeader eyebrow="Configuration" title={configuration.name} badges={<ConfigStatusBadges configuration={configuration} />}
      actions={<Link className="btn btn-secondary cfg-btn" href={routes.newConfiguration(configuration.id)}>Edit configuration</Link>}
      meta={`${robots.length} robots · Test ${robots.filter(robot => robot.role === "test").length} · Production ${robots.filter(robot => robot.role === "production").length} · ${fmtUpdated(configuration.updatedAt)}`} />
    <WorkspaceSourceNotice />
    <WorkspaceNotice workspace={workspace} configId={configuration.id} live={live} />
    {flagged.length > 0 && <Notice tone="warning" icon="flag"><strong>{flagged.length} {flagged.length === 1 ? "robot is" : "robots are"} flagged:</strong> {flagged.map(entry => `${entry.robot.name} (${entry.flags[0].label})`).join(" · ")}</Notice>}
    <PagePlaceholder title="Configuration dashboard" items={[
      "Tabs: Overview · Robots · Logs · Specification",
      "Production KPIs, connected-device panel, telemetry small multiples, edge vs cloud latency",
      "Robots table, specification panels, model logs, Add robot dialog",
    ]} />
    <ul className="cfg-placeholder-list" aria-label="Robots">
      {robots.map(robot => {
        const readings = robotReadings(robot, getRevision(configuration, robot.rev), live[robot.id], now);
        const href = robotHref(robot);
        return <li key={robot.id}>
          {href ? <Link className="portal-table-link" href={href}>{robot.name}</Link> : robot.name}
          <RoleBadge role={robot.role} /><HealthBadge health={readings.health} /><ProvenanceBadge provenance={readings.provenance} now={now} />
        </li>;
      })}
    </ul>
  </AppShell>;
}
