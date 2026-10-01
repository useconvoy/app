"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { getSuite, resolveRunRoute, runHref, useWorkspace } from "@/lib/configurations/client";
import { clockName, comparisonRun, fmtDuration, gateThreshold, hilRunFor, otherConfigurationRun, runningElapsed, secondsBetween } from "@/lib/configurations/eval-run";
import { fmtCount, fmtDateTime, runLabel } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { ID_PATTERN } from "@/lib/configurations/types";
import type { Configuration, ConvoyWorkspace, EvalRun, Robot } from "@/lib/configurations/types";
import type { EvaluationRun } from "@/lib/platform/client";
import { AppShell, type Crumb } from "../AppShell";
import { ProvenanceBadge, RoleBadge, RunStatus } from "../Badges";
import { useNow } from "../hooks";
import { Notice, WorkspaceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { LoadingState, NotFoundState } from "../States";
import { usePlatformRead, useRunQuery } from "./eval-run/hooks";
import { ReplayView, resolveReplay } from "./eval-run/Replay";
import { RerunDialog } from "./eval-run/RerunDialog";
import { RolloutsSection } from "./eval-run/RolloutsSection";
import {
  AboutRun, ComparePanel, FailurePanels, GatePanel, NoResultsYet, RecordedEpisodePanel, RecordedEvaluationPanel, RunMetrics, RunProgress, SlicesSection, TaskResults,
} from "./eval-run/RunSections";

/**
 * Screen 4 · Evaluation run (`/app/configurations/[configId]/robots/[robotId]/evals/[runId]`).
 * `?rollout=` replaces the page body with the rollout replay (full width, as the mock);
 * `?compare=` shows the comparison with the production-revision run; `?outcome=`,
 * `?slice=` and `?task=` filter the rollouts table.
 */
export function EvalRunPage({ configId, robotId, runId }: { configId: string; robotId: string; runId: string }) {
  const ws = useWorkspace();
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
  return <RunView key={route.run.id} workspace={workspace} configuration={route.configuration} robot={route.robot} run={route.run} runConfiguration={route.runConfiguration} />;
}

function RunView({ workspace, configuration, robot, run, runConfiguration }: { workspace: ConvoyWorkspace; configuration: Configuration; robot: Robot; run: EvalRun; runConfiguration: Configuration | null }) {
  const ws = useWorkspace();
  const now = useNow(30_000);
  const base = routes.run(configuration.id, robot.id, run.id);
  const query = useRunQuery(base);
  const suite = getSuite(workspace, run.suiteId);
  const hasResults = run.counts.episodes > 0;
  // Compare only when there is something to compare: a queued run has no figures yet.
  const baseline = hasResults ? comparisonRun(workspace, run) : null;
  const comparing = baseline && query.get("compare") === baseline.id ? baseline : null;
  const evaluationId = run.recordedEvaluationId && ID_PATTERN.test(run.recordedEvaluationId) ? run.recordedEvaluationId : null;
  const evaluation = usePlatformRead<EvaluationRun>(evaluationId ? `evaluations/${evaluationId}` : null);
  const replayId = query.get("rollout");
  const [rerunOpen, setRerunOpen] = useState(false);
  const [queued, setQueued] = useState<EvalRun | null>(null);

  /* Replay open/close: opening pushes `?rollout=`; closing goes back when this page opened it, and focus returns to the opener. */
  const opener = useRef<HTMLElement | null>(null);
  const openedHere = useRef(false);
  const shown = useRef<string | null>(null);
  const { update } = query;
  const openReplay = useCallback((event: MouseEvent<HTMLAnchorElement>, id: string) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    opener.current = event.currentTarget;
    openedHere.current = true;
    update({ rollout: id }, { push: true });
  }, [update]);
  const closeReplay = useCallback(() => {
    if (openedHere.current) { openedHere.current = false; window.history.back(); return; }
    update({ rollout: null }, { push: true });
  }, [update]);
  useEffect(() => {
    if (replayId) { shown.current = replayId; return; }
    const closed = shown.current;
    if (!closed) return;
    shown.current = null;
    openedHere.current = false;
    const target = opener.current?.isConnected ? opener.current : document.querySelector<HTMLElement>(`[data-replay-link="${CSS.escape(closed)}"]`);
    opener.current = null;
    const restore = () => (target?.isConnected ? target : document.getElementById("main"))?.focus();
    restore();
    // Going back to an entry with a #fragment (the slices' "Filter rollouts" links) lets the browser move focus to
    // the page after this effect, while it scrolls to the fragment: put it back on the opener once that has happened.
    let frame = window.requestAnimationFrame(() => {
      frame = window.requestAnimationFrame(() => { if (!document.activeElement || document.activeElement === document.body) restore(); });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [replayId]);

  /* Comparison: the toggle keeps Back working and moves focus to the panel it opens. */
  const focusCompare = useRef(false);
  const setCompareHeading = useCallback((node: HTMLHeadingElement | null) => {
    if (node && focusCompare.current) { focusCompare.current = false; node.focus(); }
  }, []);
  const toggleCompare = () => {
    if (!baseline) return;
    focusCompare.current = !comparing;
    update({ compare: comparing ? null : baseline.id }, { push: true });
  };

  const root: Crumb = { label: "Configurations", href: routes.index() };
  const runCrumbs: Crumb[] = [root, { label: configuration.name, href: routes.configuration(configuration.id) }, { label: robot.name, href: routes.robot(configuration.id, robot.id) }];
  const replayHref = (id: string) => query.href({ rollout: id });
  const replay = replayId ? resolveReplay(workspace, run, suite, replayId, evaluation.state) : null;
  const crumbs: Crumb[] = replay ? [...runCrumbs, { label: runLabel(run), href: query.href({ rollout: null }) }, { label: replayId! }] : [...runCrumbs, { label: runLabel(run) }];

  const isSuite = run.kind === "suite" && !!suite;
  const active = run.status === "queued" || run.status === "running";
  const started = run.startedAt !== null;
  const hasRollouts = workspace.rollouts.some(rollout => rollout.runId === run.id);
  const rerunnable = isSuite && !active;
  const sliceGate = gateThreshold(suite, "slice");
  const other = isSuite && run.gate ? otherConfigurationRun(workspace, run) : null;
  const configName = runConfiguration?.name ?? run.configId;
  // A running run's duration is the one its stored progress was reported at, never "now minus start".
  const running = runningElapsed(run);
  const wall = run.finishedAt ? fmtDuration(secondsBetween(run.startedAt, run.finishedAt)) : running ? fmtDuration(running.seconds) : null;
  const design = suite ? (() => {
    const slices = suite.sliceFamilies.reduce((sum, family) => sum + family.slices.length, 0);
    const product = suite.tasks.length * Math.max(1, slices) * suite.seedsPerCell;
    return `${suite.name} ${suite.version} · ${product === suite.episodesPerRun ? `${suite.tasks.length} tasks × ${slices} slices × ${suite.seedsPerCell} seeds = ` : ""}${fmtCount(suite.episodesPerRun)} episodes`;
  })() : null;
  const meta = [
    run.purpose ?? null, `${configName} ${run.rev}`, `${run.variant} on ${robot.name}`, design,
    run.episodeTime ? `Episode times on the ${clockName(run.episodeTime.clock)}` : null,
    started ? `Started ${fmtDateTime(run.startedAt)}` : "Not started",
    wall ? `${wall} wall-clock${running ? ` as of ${fmtDateTime(running.asOf)}` : ""}` : null,
  ].filter(Boolean).join(" · ");
  const queuedHref = queued ? runHref(ws.workspace ?? workspace, queued) : null;

  return <AppShell crumbs={crumbs} context={<RoleBadge role={robot.role} />}>
    <div className="ev-run" hidden={!!replay}>
      <PageHeader eyebrow="Evaluation run" title={`${runLabel(run)} · ${run.title}`}
        badges={<><RunStatus run={run} /><ProvenanceBadge provenance={run.provenance} now={now} /></>}
        actions={(baseline || rerunnable) ? <>
          {baseline && <button className="btn btn-secondary cfg-btn ev-toggle" type="button" aria-pressed={!!comparing} aria-controls={comparing ? "compare" : undefined} onClick={toggleCompare}>Compare with {runLabel(baseline)} ({baseline.rev})</button>}
          {rerunnable && <button className="btn btn-primary cfg-btn" type="button" onClick={() => setRerunOpen(true)}>Re-run</button>}
        </> : undefined}
        meta={meta} />
      <WorkspaceNotice workspace={workspace} configId={configuration.id} />
      {queued && <div role="status"><Notice tone="info" icon="clock" action={queuedHref ? <Link className="btn btn-secondary cfg-btn" href={queuedHref}>Open {runLabel(queued)}</Link> : undefined}>
        <strong>{runLabel(queued)} is queued</strong> · {queued.title} on {configName} {queued.rev}. No evaluation runner is connected, so it stays queued with no results until a runner reports them.
      </Notice></div>}
      <RunProgress run={run} />
      {(hasResults || !active) && <RunMetrics workspace={workspace} run={run} suite={suite} robot={robot} configuration={runConfiguration} baseline={baseline}
        hilRun={run.hilLatency ? run : isSuite ? hilRunFor(workspace, run.robotId) : null} gateShare={gateThreshold(suite, "overall")} now={now} />}
      {comparing && <ComparePanel run={run} baseline={comparing} suite={suite} configuration={runConfiguration} headingRef={setCompareHeading} />}
      {isSuite && <GatePanel suite={suite} run={run} baseline={baseline} configuration={runConfiguration} now={now} />}
      {isSuite && !hasResults && <NoResultsYet run={run} />}
      {isSuite && hasResults && <>
        <TaskResults run={run} suite={suite} baseline={baseline} now={now} />
        {run.slices.length > 0 && <SlicesSection workspace={workspace} run={run} suite={suite} other={other} gateShare={sliceGate} sliceFilter={query.get("slice")}
          filterHref={hasRollouts ? sliceId => `${query.href({ slice: sliceId, outcome: null, task: null, rollout: null })}#rollouts` : undefined}
          onToggleSlice={sliceId => update({ slice: query.get("slice") === sliceId ? null : sliceId, outcome: null, task: null })}
          replayFor={rollout => replayHref(rollout.id)} onOpenReplay={event => openReplay(event, event.currentTarget.dataset.replayLink ?? "")} now={now} />}
        <FailurePanels run={run} suite={suite} now={now} />
        <RolloutsSection workspace={workspace} run={run} suite={suite} query={query} replayHref={replayHref} onOpenReplay={openReplay} now={now} />
      </>}
      {!isSuite && <>
        <AboutRun run={run} robot={robot} now={now} />
        {run.failureModes.length > 0 && <FailurePanels run={run} suite={null} now={now} />}
      </>}
      {evaluationId && <RecordedEvaluationPanel evaluationId={evaluationId} state={evaluation.state} onRetry={evaluation.retry} replayHref={replayHref}
        onOpenReplay={event => openReplay(event, event.currentTarget.dataset.replayLink ?? "")} />}
      {run.recordedEpisodeId && ID_PATTERN.test(run.recordedEpisodeId) && <RecordedEpisodePanel run={run} href={replayHref(run.recordedEpisodeId)}
        onOpenReplay={event => openReplay(event, run.recordedEpisodeId!)} now={now} />}
      {rerunnable && suite && <RerunDialog open={rerunOpen} onClose={() => setRerunOpen(false)} run={run} suite={suite} robot={robot} configName={configName}
        onQueued={value => { setQueued(value); setRerunOpen(false); }} />}
    </div>
    {replay && <ReplayView key={replay.id} workspace={workspace} run={run} suite={suite} configuration={runConfiguration} pageConfigId={configuration.id} target={replay} onClose={closeReplay} now={now} />}
  </AppShell>;
}
