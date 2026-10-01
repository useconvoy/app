"use client";

import { getSuite, resolveRunRoute, rolloutsFor, successShare, useWorkspace } from "@/lib/configurations/client";
import { fmtCi, fmtDateTime, fmtRatio, fmtShare, runLabel } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { AppShell, type Crumb } from "../AppShell";
import { ProvenanceBadge, RunStatus } from "../Badges";
import { useNow } from "../hooks";
import { WorkspaceNotice, WorkspaceSourceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { LoadingState, NotFoundState, PagePlaceholder } from "../States";

/**
 * Screen 4 · Evaluation run (`/app/configurations/[configId]/robots/[robotId]/evals/[runId]`,
 * `?rollout=` opens the replay drawer). Page body: work package 4.
 */
export function EvalRunPage({ configId, robotId, runId }: { configId: string; robotId: string; runId: string }) {
  const ws = useWorkspace();
  const now = useNow();
  const root: Crumb = { label: "Configurations", href: routes.index() };
  if (ws.status === "loading") return <AppShell crumbs={[root, { label: "Evaluation run" }]}><LoadingState /></AppShell>;
  const workspace = ws.workspace;
  const route = resolveRunRoute(workspace, configId, robotId, runId);
  if (!route) {
    const robotKnown = workspace.robots.some(robot => robot.id === robotId && robot.configId === configId);
    return <AppShell crumbs={[root, { label: runId }]}>
      <NotFoundState title="This run is not in the workspace." text={`No evaluation run “${runId}” was recorded on “${robotId}”.`} href={robotKnown ? routes.robot(configId, robotId) : routes.index()} linkLabel={robotKnown ? "Back to the robot" : "Back to configurations"} />
    </AppShell>;
  }
  const { configuration, robot, run, runConfiguration } = route;
  const suite = getSuite(workspace, run.suiteId);
  const rollouts = rolloutsFor(workspace, run.id);
  const share = successShare(run);
  return <AppShell crumbs={[root, { label: configuration.name, href: routes.configuration(configuration.id) }, { label: robot.name, href: routes.robot(configuration.id, robot.id) }, { label: runLabel(run) }]}>
    <PageHeader eyebrow="Evaluation run" title={`${runLabel(run)} · ${run.title}`}
      badges={<><RunStatus run={run} /><ProvenanceBadge provenance={run.provenance} now={now} /></>}
      meta={[suite ? `${suite.name} ${suite.version}` : null, `${runConfiguration?.name ?? run.configId} ${run.rev}`, run.variant, robot.name, run.startedAt ? `Started ${fmtDateTime(run.startedAt)}` : "Not started"].filter(Boolean).join(" · ")} />
    <WorkspaceSourceNotice />
    <WorkspaceNotice workspace={workspace} configId={configuration.id} />
    <PagePlaceholder title="Evaluation run" items={[
      "Metric tiles: success with n and 95 % CI against the gate, safety per 100, median episode time, change vs baseline",
      "Scenario slices matrix, failure modes, sim rollouts table",
      "Replay drawer (?rollout=), real replay for recorded episodes",
    ]} />
    <dl className="cfg-placeholder-list" aria-label="Run summary">
      <div><dt>Success</dt><dd>{fmtShare(share)} · {fmtRatio(run.counts.successes, run.counts.episodes)} · 95 % CI {fmtCi(run.successCi95)}</dd></div>
      <div><dt>Rollouts recorded</dt><dd>{rollouts.length}</dd></div>
    </dl>
  </AppShell>;
}
