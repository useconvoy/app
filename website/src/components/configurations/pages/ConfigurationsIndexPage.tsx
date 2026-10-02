"use client";

import Link from "next/link";
import { ProjectConfigurationsRow } from "@/components/projects/ProjectConfigurations";
import { useMemo } from "react";
import { currentRevision, listConfigurations, useWorkspace } from "@/lib/configurations/client";
import { emptyWorkspace } from "@/lib/configurations/mutations";
import { routes } from "@/lib/configurations/routes";
import { latestScored, runShare } from "@/lib/configurations/runs";
import { fmtShare } from "@/lib/configurations/format";
import type { Configuration } from "@/lib/configurations/types";
import { AppShell, PageHeader, type Crumb } from "../AppShell";
import { ConfigStatusBadge, ResultBadge } from "../Badges";
import { useNow } from "../hooks";
import { ImportButton, WorkspaceNotice } from "../Notice";
import { EmptyState, LoadingState } from "../States";
import { useRobotViews, type RobotView } from "../useRobots";

const CRUMBS: Crumb[] = [{ label: "Configurations" }];
const EMPTY = emptyWorkspace(0);
const models = (items: ReadonlyArray<{ shortName: string }>) => items.map(item => item.shortName).join(" · ");

/** Configurations (`/app/configurations`): one card per configuration, oldest first; one row for the projects' runnable configurations. */
export function ConfigurationsIndexPage() {
  const ws = useWorkspace();
  const now = useNow();
  const workspace = ws.workspace ?? EMPTY;
  const robots = useMemo(() => workspace.robots.filter(robot => robot.configId !== null), [workspace]);
  const { views } = useRobotViews(workspace, robots, now);
  if (ws.status === "loading") return <AppShell crumbs={CRUMBS}><LoadingState /></AppShell>;
  const configurations = listConfigurations(workspace);
  const create = <Link className="cv-btn cv-btn--primary" href={routes.newConfiguration()}>New configuration</Link>;
  return <AppShell crumbs={CRUMBS}>
    <PageHeader title="Configurations" actions={<><ImportButton />{create}</>} />
    <WorkspaceNotice />
    {configurations.length
      ? <div className="cv-grid">{configurations.map(config => <ConfigCard key={config.id} config={config} robots={views.filter(view => view.robot.configId === config.id)} />)}</div>
      : <EmptyState title="No configurations yet." action={create} />}
    <ProjectConfigurationsRow />
  </AppShell>;
}

function ConfigCard({ config, robots }: { config: Configuration; robots: RobotView[] }) {
  const revision = currentRevision(config);
  const latest = latestScored(robots.flatMap(view => view.runs));
  const running = robots.flatMap(view => view.runs).find(run => run.result === "running");
  const online = robots.filter(view => view.status === "online").length;
  return <Link className="cv-config" href={routes.configuration(config.id)}>
    <div className="cv-config__head"><h2>{config.name}</h2><ConfigStatusBadge status={config.status} /></div>
    <dl className="cv-config__facts">
      <div><dt>Robot</dt><dd>{revision.robot.name}</dd></div>
      <div><dt>Edge</dt><dd>{revision.edgeHardware.name}</dd></div>
      <div><dt>Edge model</dt><dd>{models(revision.edgeModels) || "–"}</dd></div>
      <div><dt>Cloud model</dt><dd>{models(revision.cloudModels) || "–"}</dd></div>
    </dl>
    <div className="cv-config__foot">
      <span>{robots.length === 1 ? "1 robot" : `${robots.length} robots`}{online > 0 && <span className="cv-live"><i className="cv-dot" aria-hidden="true" />{online} online</span>}</span>
      {running ? <ResultBadge result="running" progress={running.progress} />
        : latest ? <span className="cv-config__eval">{fmtShare(runShare(latest), 0)}<ResultBadge result={latest.result} /></span>
          : <span className="cv-muted">No evals</span>}
    </div>
  </Link>;
}
