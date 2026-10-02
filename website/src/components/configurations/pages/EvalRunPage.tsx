"use client";

import { useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { EpisodeReplay } from "../EpisodeReplay";
import { getSuite, resolveRobotRoute, useWorkspace } from "@/lib/configurations/client";
import { fmtCount, fmtFixed, fmtNumber, fmtSeconds, fmtWhen } from "@/lib/configurations/format";
import { emptyWorkspace } from "@/lib/configurations/mutations";
import { routes } from "@/lib/configurations/routes";
import {
  belongsTo, evaluationRollouts, evaluationView, medianSteps, missionRollouts, missionView, OFFLINE_TAG, offlineMetrics, offlineRollouts, offlineView, platformPaths,
  runShare, seedSlices, sliceViews, storedRunRollouts, storedRunView,
} from "@/lib/configurations/runs";
import type { MetricView, PlatformEvaluation, PlatformMission, RolloutView, RunView, SliceView } from "@/lib/configurations/runs";
import type { Robot } from "@/lib/configurations/types";
import type { Episode, OfflineEvaluationDetail } from "@/lib/platform/client";
import { AppShell, PageHeader, type Crumb } from "../AppShell";
import { Missing, ResultBadge, RolloutBadge, Tag } from "../Badges";
import { DataTable, type Column } from "../DataTable";
import { useNow, useQueryState } from "../hooks";
import { Icon } from "../Icons";
import { Notice, WorkspaceNotice } from "../Notice";
import { Sheet } from "../Overlay";
import { usePlatform } from "../platform";
import { EmptyState, LoadingState, NotFoundState } from "../States";
import { TabPanel, Tabs, useQueryTab, type TabItem } from "../Tabs";
import { Card, Facts, Tile, Tiles } from "../Tiles";
import { useRobotViews } from "../useRobots";
import { EvalEvidence } from "../evidence/EvidenceRow";

const EMPTY = emptyWorkspace(0);
/** "Seed 0" or "Seeds 0–9". */
function seeds(rows: readonly RolloutView[]): string | null {
  const values = [...new Set(rows.map(row => row.seed).filter((seed): seed is number => seed !== null))].toSorted((a, b) => a - b);
  return !values.length ? null : values.length === 1 ? `Seed ${values[0]}` : `Seeds ${values[0]}–${values[values.length - 1]}`;
}
const TABS: TabItem[] = [{ id: "overview", label: "Overview" }, { id: "details", label: "Details" }];

interface RunModel {
  view: RunView;
  rollouts: RolloutView[];
  slices: SliceView[];
  /** Which clock the median time is on. */
  clock: "Wall clock" | "Simulated" | null;
  /** What the episodes cover: the suite, or the seeds. */
  scope: string | null;
  details: Array<{ label: string; value: ReactNode | null }>;
  /** Offline evaluations: the mean of each numeric metric the episodes report. */
  metrics?: MetricView[];
}

/**
 * Eval page (`…/evals/[runId]`): result, metric tiles, slices and rollouts; the ids
 * under Details. `runId` is a stored run, or a platform evaluation (`eva_…`) or
 * mission (`mis_…`) of the robot's project, or an offline evaluation (`oev_…`) the
 * robot links, read through the proxy. A rollout with a real episode opens the
 * replay in a bottom panel (`?rollout=`); an offline one plays its uploaded frames.
 */
export function EvalRunPage({ configId, robotId, runId }: { configId: string; robotId: string; runId: string }) {
  const ws = useWorkspace();
  const now = useNow();
  const workspace = ws.workspace ?? EMPTY;
  const route = resolveRobotRoute(workspace, configId, robotId);
  const robots = useMemo(() => { const found = resolveRobotRoute(workspace, configId, robotId)?.robot; return found ? [found] : []; }, [workspace, configId, robotId]);
  const robot = route?.robot ?? null;
  const { views } = useRobotViews(workspace, robots, now);
  const stored = robot ? workspace.runs.find(run => run.id === runId && run.robotId === robot.id) ?? null : null;
  const kind = stored ? "document" : robot?.projectId && /^eva_/.test(runId) ? "evaluation" : robot?.projectId && /^mis_/.test(runId) ? "mission"
    : /^oev_/.test(runId) && robot?.offlineEvaluationIds?.includes(runId) ? "offline" : null;
  const evaluationId = stored?.recordedEvaluationId ?? (kind === "evaluation" ? runId : null);
  const evaluation = usePlatform<PlatformEvaluation>(evaluationId ? platformPaths.evaluation(evaluationId) : null);
  const mission = usePlatform<PlatformMission>(kind === "mission" ? platformPaths.mission(runId) : null);
  const offline = usePlatform<OfflineEvaluationDetail>(kind === "offline" ? platformPaths.offlineEvaluation(runId) : null);
  const missionEpisode = mission.state.status === "ready" ? mission.state.data.episode_id : null;
  const episode = usePlatform<Episode>(missionEpisode ? platformPaths.episode(missionEpisode) : null);
  // A standalone episode's wall time comes with its recording's metadata.
  const recording = usePlatform<{ wall_seconds: number | null }>(missionEpisode ? platformPaths.replay(missionEpisode) : null);

  const root: Crumb = { label: "Configurations", href: routes.index() };
  if (ws.status === "loading") return <AppShell crumbs={[root, { label: "Eval" }]}><LoadingState /></AppShell>;
  if (!route || !robot) return <AppShell crumbs={[root, { label: runId }]}><NotFoundState title="Robot not found." href={routes.index()} linkLabel="All configurations" /></AppShell>;
  const crumbs = (label: string): Crumb[] => [root, { label: route.configuration.name, href: routes.configuration(configId) }, { label: robot.name, href: routes.robot(configId, robot.id) }, { label }];
  const notFound = <AppShell crumbs={crumbs(runId)}><NotFoundState title="Eval not found." href={routes.robot(configId, robot.id)} linkLabel={`Back to ${robot.name}`} /></AppShell>;
  const number = views[0]?.runs.find(view => view.id === runId)?.number ?? null;

  let model: RunModel | null = null;
  if (stored) {
    const suite = getSuite(workspace, stored.suiteId);
    const recorded = evaluation.state.status === "ready" && evaluation.state.data.report ? evaluation.state.data : null;
    const base = storedRunView(stored);
    const view = recorded ? { ...evaluationView(recorded, base.number), id: base.id, source: base.source, run: stored } : base;
    const rollouts = storedRunRollouts(workspace, stored, suite, evaluation.state.status === "ready" ? evaluation.state.data : null);
    const slices = stored.slices.length ? sliceViews(suite, stored.slices) : seedSlices(rollouts);
    model = {
      view, rollouts, slices, clock: recorded ? "Wall clock" : stored.episodeTime ? (stored.episodeTime.clock === "simulated" ? "Simulated" : "Wall clock") : null,
      scope: suite ? `${suite.name} ${suite.version}` : stored.title,
      details: [
        { label: "Suite", value: suite ? `${suite.name} ${suite.version}` : stored.title },
        { label: "Revision", value: stored.rev },
        { label: "Started", value: stored.startedAt ? `${fmtWhen(stored.startedAt)} UTC` : null },
        { label: "Finished", value: stored.finishedAt ? `${fmtWhen(stored.finishedAt)} UTC` : null },
        { label: "Evaluation", value: stored.recordedEvaluationId ? <span className="cv-mono">{stored.recordedEvaluationId}</span> : null },
        { label: "Source", value: stored.provenance.kind === "sample" ? "Sample" : stored.provenance.kind === "recorded" ? "Recorded" : "Workspace" },
      ],
    };
  } else if (kind === "evaluation") {
    if (evaluation.state.status === "error") return evaluation.state.missing ? notFound : <AppShell crumbs={crumbs("Eval")}><Notice tone="error" action={<button className="cv-link" type="button" onClick={evaluation.retry}>Retry</button>}>{evaluation.state.message}</Notice></AppShell>;
    if (evaluation.state.status === "ready") {
      const data = evaluation.state.data;
      if (!belongsTo(robot, data)) return notFound;
      const rollouts = evaluationRollouts(data);
      model = {
        view: evaluationView(data, number ?? 0), rollouts, slices: seedSlices(rollouts), clock: "Wall clock", scope: seeds(rollouts),
        details: [
          { label: "Evaluation", value: <span className="cv-mono">{data.id}</span> },
          { label: "Suite", value: <span className="cv-mono">{data.suite_id}</span> },
          { label: "Release", value: <span className="cv-mono">{data.release_id}</span> },
          { label: "Platform robot", value: <span className="cv-mono">{data.robot_id}</span> },
          { label: "Created", value: data.created_at ? `${fmtWhen(data.created_at)} UTC` : null },
          { label: "Updated", value: `${fmtWhen(data.updated_at)} UTC` },
        ],
      };
    }
  } else if (kind === "mission") {
    if (mission.state.status === "error") return mission.state.missing ? notFound : <AppShell crumbs={crumbs("Eval")}><Notice tone="error" action={<button className="cv-link" type="button" onClick={mission.retry}>Retry</button>}>{mission.state.message}</Notice></AppShell>;
    if (mission.state.status === "ready") {
      const data = mission.state.data;
      if (!belongsTo(robot, data)) return notFound;
      const read = episode.state.status === "ready" ? episode.state.data : null;
      const waiting = !!data.episode_id && episode.state.status === "loading";
      const wall = recording.state.status === "ready" && typeof recording.state.data.wall_seconds === "number" ? recording.state.data.wall_seconds : null;
      model = {
        view: { ...missionView(data, read, number ?? 0), medianS: wall }, rollouts: waiting ? [] : missionRollouts(data, read).map(row => ({ ...row, seconds: wall })),
        slices: [], clock: wall === null ? null : "Wall clock", scope: `Seed ${data.seed}`,
        details: [
          { label: "Mission", value: <span className="cv-mono">{data.id}</span> },
          { label: "Episode", value: data.episode_id ? <span className="cv-mono">{data.episode_id}</span> : null },
          { label: "Release", value: <span className="cv-mono">{data.release_id}</span> },
          { label: "Seed", value: String(data.seed) },
          { label: "Updated", value: `${fmtWhen(data.updated_at)} UTC` },
        ],
      };
    }
  } else if (kind === "offline") {
    if (offline.state.status === "error") return offline.state.missing ? notFound : <AppShell crumbs={crumbs("Eval")}><Notice tone="error" action={<button className="cv-link" type="button" onClick={offline.retry}>Retry</button>}>{offline.state.message}</Notice></AppShell>;
    if (offline.state.status === "ready") {
      const data = offline.state.data;
      const rollouts = offlineRollouts(data);
      const summary = data.summary;
      model = {
        view: offlineView(data, number ?? 0), rollouts, slices: seedSlices(rollouts), scope: seeds(rollouts), metrics: offlineMetrics(data.episodes),
        clock: summary.median_wall_seconds !== null ? "Wall clock" : summary.median_sim_seconds !== null ? "Simulated" : null,
        details: [
          { label: "Name", value: data.name },
          { label: "Task", value: data.task },
          { label: "Configuration", value: data.config_label },
          { label: "Policy", value: data.policy_label },
          { label: "Measurement source", value: [...new Set(data.episodes.flatMap(item => typeof item.metrics.measurement_source === "string" ? [item.metrics.measurement_source] : []))].join("; ") || null },
          { label: "Source", value: <span title={data.scope}>Offline import · unsigned</span> },
          { label: "Offline evaluation", value: <span className="cv-mono">{data.id}</span> },
          { label: "Imported", value: `${fmtWhen(data.created_at)} UTC` },
          { label: "Updated", value: `${fmtWhen(data.updated_at)} UTC` },
        ],
      };
    }
  } else {
    return notFound;
  }
  if (!model) return <AppShell crumbs={crumbs(number ? `Eval ${number}` : "Eval")}><LoadingState label="Loading eval…" /></AppShell>;
  if (number === null && model.view.source !== "document") model = { ...model, view: { ...model.view, label: "Eval" } };
  return <EvalRun crumbs={crumbs(model.view.label)} model={model} robot={robot} />;
}

function EvalRun({ crumbs, model, robot }: { crumbs: Crumb[]; model: RunModel; robot: Robot }) {
  const [tab, setTab] = useQueryTab(TABS);
  const { view, rollouts, slices, metrics } = model;
  const share = runShare(view);
  const steps = medianSteps(rollouts);
  const tag = view.source === "offline" ? <Tag title={view.offline?.scope}>{OFFLINE_TAG}</Tag> : null;

  /* Replay in `?rollout=`: opening pushes (Back closes it); closing goes back when this page pushed, else replaces. */
  const [rolloutId, setRolloutId] = useQueryState("rollout");
  const pushed = useRef<string | null>(null);
  const replay = rolloutId ? rollouts.find(row => row.id === rolloutId) ?? null : null;
  function open(id: string) { pushed.current = id; setRolloutId(id); }
  function close() {
    if (rolloutId !== null && pushed.current === rolloutId) { pushed.current = null; window.history.back(); }
    else setRolloutId(null, { replace: true });
  }
  // A replay opened from the URL has no opener to return focus to: use its row's Replay button.
  const last = useRef<string | null>(rolloutId);
  useEffect(() => {
    const previous = last.current;
    last.current = rolloutId;
    if (!previous || rolloutId) return;
    const active = document.activeElement;
    if (active && active !== document.body) return;
    document.querySelector<HTMLElement>(`[data-replay="${CSS.escape(previous)}"]`)?.focus({ preventScroll: true });
  }, [rolloutId]);

  const columns: Array<Column<RolloutView>> = [
    { key: "episode", header: rollouts.some(row => row.task) ? "Task" : "Episode", cell: row => row.task ?? <span className="cv-mono">{row.episodeId ?? row.id}</span> },
    { key: "seed", header: "Seed", numeric: true, wide: true, cell: row => row.seed === null ? <Missing /> : String(row.seed) },
    { key: "steps", header: "Steps", numeric: true, wide: true, cell: row => row.steps === null ? <Missing /> : fmtCount(row.steps) },
    { key: "time", header: "Time", numeric: true, wide: true, cell: row => row.seconds === null ? <Missing /> : fmtSeconds(row.seconds) },
    { key: "result", header: "Result", cell: row => <RolloutBadge result={row.result} /> },
    { key: "replay", header: "Replay", srOnly: true, cell: row => row.episodeId
      ? <button className="cv-btn cv-btn--small" type="button" data-replay={row.id} aria-label={`Replay ${row.task ?? row.episodeId}${row.seed !== null ? `, seed ${row.seed}` : ""}`} onClick={() => open(row.id)}><Icon name="play" small />Replay</button>
      : null },
  ];

  return <AppShell crumbs={crumbs}>
    <PageHeader title={view.label} badges={<><ResultBadge result={view.result} progress={view.progress} />{tag}</>} />
    <WorkspaceNotice />
    <Tiles label="Results">
      <Tile label="Success rate" value={share === null ? null : fmtFixed(share * 100, 0)} unit="%" sub={view.successes !== null && view.episodes ? `${view.successes} of ${view.episodes}` : undefined} />
      <Tile label="Episodes" value={view.episodes === null ? null : fmtCount(view.episodes)} sub={view.progress ? `of ${view.progress.total}` : model.scope ?? undefined} />
      <Tile label="Median time" value={view.medianS === null ? null : fmtFixed(view.medianS, 1)} unit="s" sub={model.clock ?? undefined} />
      <Tile label="Median steps" value={steps === null ? null : fmtCount(Math.round(steps))} sub={rollouts.length ? `${rollouts.length} ${rollouts.length === 1 ? "rollout" : "rollouts"}` : undefined} />
    </Tiles>
    <Tabs tabs={TABS} value={tab} onChange={setTab} label="Eval views" idPrefix="ev" />
    <TabPanel idPrefix="ev" tabId="overview" selected={tab === "overview"}>
      <EvalEvidence robot={robot} view={view} />
      {slices.length > 0 && <Card title="Slices" flush><SliceTable rows={slices} /></Card>}
      {!!metrics?.length && <Card title="Metrics" flush>{view.source === "offline" && <p className="cv-muted">Reported simulator measurements, averaged per episode. Imported results do not qualify robot or cloud timing.</p>}<MetricTable rows={metrics} /></Card>}
      <Card title="Rollouts" flush>
        {rollouts.length ? <DataTable label="Rollouts" columns={columns} rows={rollouts} rowKey={row => row.id} rowClass={row => row.id === rolloutId ? "cv-tr-current" : undefined} />
          : <EmptyState title={view.result === "queued" ? "Not started." : "No rollouts yet."} />}
      </Card>
    </TabPanel>
    <TabPanel idPrefix="ev" tabId="details" selected={tab === "details"}>
      <Card label="Details"><Facts items={[{ label: "Robot", value: robot.name }, ...model.details]} /></Card>
    </TabPanel>
    <Sheet open={replay?.episodeId != null} onClose={close} closeLabel="Close replay"
      title={replay ? replay.task ?? (replay.seed !== null ? `Seed ${replay.seed}` : "Episode") : "Episode"}
      meta={replay && <span className="cv-sheet__meta"><RolloutBadge result={replay.result} />{tag}<span className="cv-mono">{replay.episodeId}</span></span>}>
      {replay?.episodeId && <EpisodeReplay key={replay.episodeId} episodeId={replay.episodeId} path={replay.recording} />}
    </Sheet>
    {rolloutId && !replay?.episodeId && <Notice tone="warning" action={<button className="cv-link" type="button" onClick={close}>Dismiss</button>}>No replay for this rollout.</Notice>}
  </AppShell>;
}

/** Mean per episode of each reported metric, with how many episodes reported it. */
function MetricTable({ rows }: { rows: readonly MetricView[] }) {
  const columns: Array<Column<MetricView>> = [
    { key: "metric", header: "Metric", cell: row => <span title={row.name}>{reportedMetricLabel(row.name)}</span> },
    { key: "mean", header: "Mean", numeric: true, cell: row => fmtNumber(row.mean, /_(ms|s|m)$/.test(row.name) ? 3 : Math.abs(row.mean) >= 100 ? 0 : 2) },
    { key: "n", header: "Episodes", numeric: true, cell: row => fmtCount(row.episodes) },
  ];
  return <DataTable label="Metrics" columns={columns} rows={rows} rowKey={row => row.name} />;
}

const REPORTED_METRIC_LABELS: Record<string, string> = {
  physics_steps: "Physics steps", wall_duration_s: "Wall duration (s)", simulated_duration_s: "Simulated duration (s)",
  simulation_wall_lag_s: "Simulation lag behind wall clock (s)", dropped_scheduler_slots: "Dropped scheduler slots",
  hold_ticks: "Hold ticks", fallback_ticks: "Fallback ticks", final_target_distance_m: "Final target distance (m)",
  planner_requests: "Planner requests", accepted_plans: "Accepted plans", rejected_plans: "Rejected plans",
  planner_timeouts: "Planner timeouts", cancelled_inflight_requests: "Cancelled planner requests",
  max_planner_inflight: "Maximum planner requests in flight",
};
const REPORTED_TIMING_LABELS: Record<string, string> = {
  physics_dispatch_lag: "Physics dispatch lag", physics_completion_lag: "Physics completion lag",
  observation_to_action: "Observation to action", controller_gap: "Controller interval", planner_latency: "Planner latency",
};
function reportedMetricLabel(name: string) {
  const timing = /^(.*)_(p95|max)_ms$/.exec(name);
  if (timing && REPORTED_TIMING_LABELS[timing[1]]) return `${REPORTED_TIMING_LABELS[timing[1]]} · ${timing[2] === "max" ? "maximum" : "p95"} (ms)`;
  return REPORTED_METRIC_LABELS[name] ?? name;
}

/** Success per slice: a bar and the rate, one line each. */
function SliceTable({ rows }: { rows: readonly SliceView[] }) {
  const columns: Array<Column<SliceView>> = [
    { key: "slice", header: "Slice", cell: row => <>{row.family && <span className="cv-muted cv-slice__family">{row.family}</span>}{row.name}</> },
    { key: "bar", header: "Success", cell: row => <span className="cv-meter" aria-hidden="true"><span style={{ width: `${Math.round((row.share ?? 0) * 100)}%` }} /></span> },
    { key: "rate", header: "Rate", numeric: true, cell: row => row.share === null ? <Missing /> : `${fmtFixed(row.share * 100, 0)} %` },
    { key: "n", header: "Episodes", numeric: true, wide: true, cell: row => `${row.successes}/${row.episodes}` },
  ];
  return <DataTable label="Slices" columns={columns} rows={rows} rowKey={row => row.id} />;
}
