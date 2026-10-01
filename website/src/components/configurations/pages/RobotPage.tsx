"use client";

import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { resolveRobotRoute, tracesFor, useWorkspace } from "@/lib/configurations/client";
import { fmtCount, fmtDate, fmtFixed, fmtNumber, fmtSeconds, fmtWhen, median } from "@/lib/configurations/format";
import type { LiveInference } from "@/lib/configurations/live";
import { emptyWorkspace, removeRobot } from "@/lib/configurations/mutations";
import { routes } from "@/lib/configurations/routes";
import { latestScored, platformPaths, runShare } from "@/lib/configurations/runs";
import type { RunView } from "@/lib/configurations/runs";
import { robotType } from "@/lib/configurations/status";
import { CONFIGURED_DEVICE } from "@/lib/configurations/types";
import type { ActionTrace, Configuration, ConvoyWorkspace } from "@/lib/configurations/types";
import type { Project, Robot as PlatformRobot } from "@/lib/platform/client";
import { AppShell, PageHeader } from "../AppShell";
import { Badge, Missing, ResultBadge, StatusBadge } from "../Badges";
import { DataTable, type Column } from "../DataTable";
import { useNow } from "../hooks";
import { Notice, WorkspaceNotice } from "../Notice";
import { ConfirmDialog } from "../Overlay";
import { EmptyState, LoadingState, NotFoundState } from "../States";
import { TabPanel, Tabs, useQueryTab, type TabItem } from "../Tabs";
import { Card, Facts, Tile, Tiles } from "../Tiles";
import { usePlatform } from "../platform";
import { useRobotViews, type RobotView } from "../useRobots";
import { CpuTile, InferenceTile, MemoryTile, TemperatureTile } from "./LiveTiles";

const EMPTY = emptyWorkspace(0);
const TRACES_SHOWN = 20;

/** Robot dashboard (`…/robots/[robotId]`): metric tiles, then Evals, Traces (with a live device) and Details. */
export function RobotPage({ configId, robotId }: { configId: string; robotId: string }) {
  const ws = useWorkspace();
  const now = useNow();
  const workspace = ws.workspace ?? EMPTY;
  const route = resolveRobotRoute(workspace, configId, robotId);
  const robots = useMemo(() => { const found = resolveRobotRoute(workspace, configId, robotId)?.robot; return found ? [found] : []; }, [workspace, configId, robotId]);
  const { views, retry } = useRobotViews(workspace, robots, now);
  const root = { label: "Configurations", href: routes.index() };
  if (ws.status === "loading") return <AppShell crumbs={[root, { label: "Robot" }]}><LoadingState /></AppShell>;
  if (!route || !views[0]) {
    const config = workspace.configurations.find(item => item.id === configId);
    return <AppShell crumbs={config ? [root, { label: config.name, href: routes.configuration(config.id) }, { label: robotId }] : [root, { label: robotId }]}>
      <NotFoundState title="Robot not found." href={config ? routes.configuration(config.id) : routes.index()} linkLabel={config ? `Back to ${config.name}` : "All configurations"} />
    </AppShell>;
  }
  return <RobotDashboard key={route.robot.id} workspace={workspace} config={route.configuration} view={views[0]} retry={retry} />;
}

function RobotDashboard({ workspace, config, view, retry }: { workspace: ConvoyWorkspace; config: Configuration; view: RobotView; retry: () => void }) {
  const ws = useWorkspace();
  const router = useRouter();
  const { robot, runs } = view;
  const live = !!robot.deviceId;
  const traces = tracesFor(workspace, robot.id);
  const inference = view.live.data?.inference ?? [];
  const tabs: TabItem[] = [
    { id: "evals", label: "Evals", count: view.runsLoading ? null : runs.length },
    ...(live || traces.length ? [{ id: "traces", label: "Traces", count: live ? inference.length || null : traces.length }] : []),
    { id: "details", label: "Details" },
  ];
  // A live device with no evals opens on its traces.
  const [tab, setTab] = useQueryTab(tabs, { fallback: live && !runs.length && !view.runsLoading ? "traces" : "evals" });
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editable = ws.source === "document" && ws.canSave;
  // Names for the platform links, when the account can list them.
  const projects = usePlatform<Project[]>(robot.projectId ? platformPaths.projects() : null);
  const platformRobots = usePlatform<PlatformRobot[]>(robot.projectId && robot.platformRobotId ? platformPaths.robots(robot.projectId) : null);
  const projectName = projects.state.status === "ready" && Array.isArray(projects.state.data) ? projects.state.data.find(item => item.id === robot.projectId)?.name : undefined;
  const platformRobotName = platformRobots.state.status === "ready" && Array.isArray(platformRobots.state.data) ? platformRobots.state.data.find(item => item.id === robot.platformRobotId)?.name : undefined;

  async function remove() {
    setBusy(true); setError(null);
    const result = await ws.save(current => removeRobot(current, robot.id, Date.now()));
    setBusy(false);
    if (result.ok) router.push(routes.configuration(config.id)); else setError(result.error);
  }

  const latest = latestScored(runs);
  const share = latest ? runShare(latest) : null;
  const episodes = runs.reduce((sum, run) => sum + (run.episodes ?? 0), 0);
  const medianTime = median(runs.map(run => run.medianS));
  const release = view.live.data?.release ?? null;
  const runColumns: Array<Column<RunView>> = [
    { key: "eval", header: "Eval", cell: run => run.label },
    { key: "at", header: "Started", wide: true, cell: run => run.at ? fmtWhen(run.at) : <Missing /> },
    { key: "episodes", header: "Episodes", numeric: true, wide: true, cell: run => run.episodes === null ? <Missing /> : fmtCount(run.episodes) },
    { key: "success", header: "Success", numeric: true, cell: run => { const value = runShare(run); return value === null ? <Missing /> : `${fmtFixed(value * 100, 0)} %`; } },
    { key: "result", header: "Result", cell: run => <ResultBadge result={run.result} progress={run.progress} /> },
  ];

  return <AppShell crumbs={[{ label: "Configurations", href: routes.index() }, { label: config.name, href: routes.configuration(config.id) }, { label: robot.name }]}>
    <PageHeader title={robot.name} badges={<><StatusBadge status={view.status} /><Badge>{robotType(robot)}</Badge></>} />
    <WorkspaceNotice />
    {view.readings.health === "attention" && view.readings.healthReason && <Notice tone="warning">{view.readings.healthReason}.</Notice>}
    {live
      ? <Tiles label="Device"><InferenceTile view={view} /><CpuTile view={view} /><MemoryTile view={view} /><TemperatureTile view={view} /></Tiles>
      : <Tiles label="Evals summary">
        <Tile label="Evals" value={view.runsLoading ? null : fmtCount(runs.length)} sub={runs[0]?.at ? `Latest ${fmtDate(runs[0].at)}` : "None yet"} />
        <Tile label="Success rate" value={share === null ? null : fmtFixed(share * 100, 0)} unit="%" sub={latest ? `${latest.label} · ${latest.successes}/${latest.episodes}` : "No evals"} />
        <Tile label="Episodes" value={view.runsLoading ? null : fmtCount(episodes)} sub={runs.length ? "All evals" : undefined} />
        <Tile label="Median time" value={medianTime === null ? null : fmtFixed(medianTime, 1)} unit="s" sub={medianTime === null ? undefined : "Per episode"} />
      </Tiles>}
    <Tabs tabs={tabs} value={tab} onChange={setTab} label="Robot views" idPrefix="rb" />
    <TabPanel idPrefix="rb" tabId="evals" selected={tab === "evals"}>
      <Card label="Evals" flush>
        {view.runsError && <p className="cv-card__note" role="alert">Platform evals unavailable. <button className="cv-link" type="button" onClick={retry}>Retry</button></p>}
        {view.runsLoading && !runs.length ? <LoadingState label="Loading evals…" />
          : <DataTable label="Evals" columns={runColumns} rows={runs} rowKey={run => run.id} rowHref={run => routes.run(config.id, robot.id, run.id)} empty="No evals yet." />}
      </Card>
    </TabPanel>
    {tabs.some(item => item.id === "traces") && <TabPanel idPrefix="rb" tabId="traces" selected={tab === "traces"}>
      {live ? <InferenceTraces spans={inference} loading={view.live.status === "loading"} /> : <StoredTraces traces={traces} />}
    </TabPanel>}
    <TabPanel idPrefix="rb" tabId="details" selected={tab === "details"}>
      <Card label="Details">
        <Facts items={[
          { label: "Type", value: robotType(robot) },
          { label: "Device", value: robot.deviceId ? <span className="cv-mono">{robot.deviceId === CONFIGURED_DEVICE ? view.live.data?.deviceId ?? "Workspace device" : robot.deviceId}</span> : null },
          { label: "Model", value: release ? release.name : null },
          { label: "Runtime", value: release?.runtime ? [release.runtime, release.backend].filter(Boolean).join(" · ") : null },
          { label: "Agent", value: view.live.data?.agentVersion ?? null },
          { label: "Evals from", value: robot.projectId ? projectName ?? <span className="cv-mono">{robot.projectId}</span> : null },
          { label: "Platform robot", value: robot.platformRobotId ? platformRobotName ?? <span className="cv-mono">{robot.platformRobotId}</span> : null },
          { label: "Added", value: fmtDate(robot.registeredAt) },
        ]} />
      </Card>
      {editable && <div className="cv-danger"><button className="cv-btn cv-btn--danger" type="button" onClick={() => { setError(null); setConfirm(true); }}>Remove robot</button></div>}
    </TabPanel>
    {confirm && <ConfirmDialog title={`Remove ${robot.name}?`} action="Remove" busyAction="Removing…" busy={busy} error={error} onConfirm={() => void remove()} onClose={() => setConfirm(false)} />}
  </AppShell>;
}

/** The live device's latest inference requests (measured on the device), newest first. */
function InferenceTraces({ spans, loading }: { spans: readonly LiveInference[]; loading: boolean }) {
  const columns: Array<Column<LiveInference>> = [
    { key: "at", header: "Time (UTC)", cell: span => span.at ? fmtWhen(span.at) : <Missing /> },
    { key: "trace", header: "Trace", wide: true, cell: span => <span className="cv-mono">{span.traceId}</span> },
    { key: "latency", header: "Latency", numeric: true, cell: span => span.latencyMs === null ? <Missing /> : `${fmtNumber(span.latencyMs, 0)} ms` },
    { key: "ttft", header: "First token", numeric: true, wide: true, cell: span => span.ttftMs === null ? <Missing /> : `${fmtNumber(span.ttftMs, 0)} ms` },
    { key: "tokens", header: "Tokens", numeric: true, wide: true, cell: span => `${fmtNumber(span.tokensIn, 0)} → ${fmtNumber(span.tokensOut, 0)}` },
    { key: "status", header: "Status", cell: span => span.status ? <Badge tone={span.status === "ok" ? "success" : "warning"}>{span.status === "ok" ? "OK" : span.status}</Badge> : <Missing /> },
  ];
  return <Card label="Traces" flush>
    {loading && !spans.length ? <LoadingState label="Connecting to the device…" />
      : <DataTable label="Traces" columns={columns} rows={spans.slice(0, TRACES_SHOWN)} rowKey={span => span.traceId} empty="No traces yet." />}
  </Card>;
}

/** Action traces stored in the workspace for this robot, newest first. */
function StoredTraces({ traces }: { traces: readonly ActionTrace[] }) {
  const columns: Array<Column<ActionTrace>> = [
    { key: "at", header: "Time (UTC)", cell: trace => fmtWhen(trace.at) },
    { key: "instruction", header: "Instruction", wide: true, cell: trace => trace.instruction },
    { key: "path", header: "Path", cell: trace => trace.path === "edge" ? "Edge" : trace.path === "cloud" ? "Cloud" : "Fallback" },
    { key: "duration", header: "Duration", numeric: true, wide: true, cell: trace => trace.durationS === null ? <Missing /> : fmtSeconds(trace.durationS) },
    { key: "outcome", header: "Outcome", cell: trace => <Badge tone={trace.outcome === "succeeded" ? "success" : trace.outcome === "clarified" ? "neutral" : "warning"}>{trace.outcome === "succeeded" ? "Succeeded" : trace.outcome === "failed" ? "Failed" : trace.outcome === "escalated" ? "Escalated" : "Clarified"}</Badge> },
  ];
  return traces.length
    ? <Card label="Traces" flush><DataTable label="Traces" columns={columns} rows={traces} rowKey={trace => trace.id} /></Card>
    : <EmptyState title="No traces yet." />;
}
