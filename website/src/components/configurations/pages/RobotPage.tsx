"use client";

import Link from "next/link";
import { getRevision, resolveRobotRoute, robotReadings, runHref, runsFor, tracesFor, useWorkspace } from "@/lib/configurations/client";
import { fmtNumber, fmtUpdated, runLabel } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { AppShell, type Crumb } from "../AppShell";
import { HealthBadge, ProvenanceBadge, RoleBadge, RunStatus } from "../Badges";
import { useNow } from "../hooks";
import { useLiveRobot } from "../LiveDeviceProvider";
import { WorkspaceNotice, WorkspaceSourceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { LoadingState, NotFoundState, PagePlaceholder } from "../States";

/**
 * Screen 3 · Robot dashboard (`/app/configurations/[configId]/robots/[robotId]`):
 * test robots (`?tab=evals|traces|logs`) and production robots (`?tab=actions|logs`,
 * `?trace=` opens the trace drawer). Page body: work package 3.
 */
export function RobotPage({ configId, robotId }: { configId: string; robotId: string }) {
  const ws = useWorkspace();
  const now = useNow();
  const route = ws.workspace ? resolveRobotRoute(ws.workspace, configId, robotId) : null;
  const live = useLiveRobot(route?.robot);
  const root: Crumb = { label: "Configurations", href: routes.index() };
  if (ws.status === "loading") return <AppShell crumbs={[root, { label: "Robot" }]}><LoadingState /></AppShell>;
  if (!route) {
    return <AppShell crumbs={[root, { label: robotId }]}>
      <NotFoundState title="This robot is not in this configuration." text={`No robot “${robotId}” is attached to “${configId}”.`} href={ws.workspace.configurations.some(item => item.id === configId) ? routes.configuration(configId) : routes.index()} linkLabel="Back to the configuration" />
    </AppShell>;
  }
  const { configuration, robot } = route;
  const workspace = ws.workspace;
  const readings = robotReadings(robot, getRevision(configuration, robot.rev), live, now);
  const runs = runsFor(workspace, { robotId: robot.id });
  const traces = tracesFor(workspace, robot.id);
  return <AppShell crumbs={[root, { label: configuration.name, href: routes.configuration(configuration.id) }, { label: robot.name }]} context={<RoleBadge role={robot.role} />}>
    <PageHeader eyebrow={robot.role === "test" ? "Test robot" : "Production robot"} title={robot.name}
      badges={<><RoleBadge role={robot.role} /><HealthBadge health={readings.health} /><ProvenanceBadge provenance={readings.provenance} now={now} /></>}
      lede={robot.description} meta={fmtUpdated(readings.live && readings.latest?.at ? readings.latest.at : workspace.meta.updatedAt, robot.deviceId ? 15 : null)} />
    <WorkspaceSourceNotice />
    <WorkspaceNotice workspace={workspace} configId={configuration.id} live={{ [robot.id]: live }} />
    <PagePlaceholder title={robot.role === "test" ? "Test robot dashboard" : "Production robot dashboard"} items={robot.role === "test"
      ? ["Device board with Open chat, health strip, telemetry tiles with sparklines", "Edge planner latency and metadata panels", "Tabs: Evals & sims · Traces · Logs; Run evaluation dialog"]
      : ["Health strip, telemetry tiles, edge and cloud KPIs", "Tabs: Actions & traces · Logs", "Trace drawer with span waterfall (?trace=)"]} />
    <dl className="cfg-placeholder-list" aria-label="Latest readings">
      <div><dt>SoC temperature</dt><dd>{fmtNumber(readings.latest?.socTempC ?? null)} °C <ProvenanceBadge provenance={readings.provenance} now={now} /></dd></div>
      <div><dt>Edge planner p50</dt><dd>{fmtNumber(readings.edgeMs?.p50 ?? null, 0)} ms <ProvenanceBadge provenance={readings.edgeProvenance} now={now} /></dd></div>
      <div><dt>Action traces</dt><dd>{traces.length}</dd></div>
    </dl>
    <ul className="cfg-placeholder-list" aria-label="Evaluation runs">
      {runs.map(run => {
        const href = runHref(workspace, run);
        return <li key={run.id}>{href ? <Link className="portal-table-link" href={href}>{runLabel(run)}</Link> : runLabel(run)}<span>{run.title}</span><RunStatus run={run} /><ProvenanceBadge provenance={run.provenance} /></li>;
      })}
    </ul>
  </AppShell>;
}
