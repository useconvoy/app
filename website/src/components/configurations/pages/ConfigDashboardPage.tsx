"use client";

import { useRouter } from "next/navigation";
import { WorkspaceConfigurationLink } from "@/components/projects/WorkspaceConfigurationLink";
import { useMemo, useState } from "react";
import { currentRevision, getConfiguration, robotsFor, useWorkspace } from "@/lib/configurations/client";
import { ROUTE_LABEL } from "@/lib/configurations/create";
import { fmtCount, fmtDate, fmtFixed, fmtWhen } from "@/lib/configurations/format";
import { deleteConfiguration, emptyWorkspace, NOT_SPECIFIED } from "@/lib/configurations/mutations";
import { robotPreview, type RobotPreview } from "@/lib/configurations/previews";
import { routes } from "@/lib/configurations/routes";
import { latestScored, runShare } from "@/lib/configurations/runs";
import type { ConfigRevision, Configuration } from "@/lib/configurations/types";
import { robotType } from "@/lib/configurations/status";
import { AppShell, PageHeader, type Crumb } from "../AppShell";
import { ConfigStatusBadge, Missing, ResultBadge, StatusBadge } from "../Badges";
import { DataTable, type Column } from "../DataTable";
import { useNow } from "../hooks";
import { Notice, WorkspaceNotice } from "../Notice";
import { ConfirmDialog } from "../Overlay";
import { LoadingState, NotFoundState } from "../States";
import { TabPanel, Tabs, useQueryTab, type TabItem } from "../Tabs";
import { Card, Facts, Tile, Tiles } from "../Tiles";
import { Turntable } from "../Turntable";
import { useRobotViews, type RobotView } from "../useRobots";
import { AddRobotDialog } from "./AddRobotDialog";
import { CpuTile, InferenceTile, MemoryTile, PowerTile, TemperatureTile } from "./LiveTiles";

const ROOT: Crumb = { label: "Configurations", href: routes.index() };
const EMPTY = emptyWorkspace(0);
const models = (items: ReadonlyArray<{ name: string; shortName: string; role: string }>, short = false) => items.map(item => `${short ? item.shortName : item.name} (${item.role})`).join(", ");

/**
 * Config dashboard (`/app/configurations/[configId]`): four KPI tiles, the live device's telemetry, the robots; the
 * specification under Details; below them, the robot turning beside its facts when the robot names a preview.
 */
export function ConfigDashboardPage({ configId }: { configId: string }) {
  const ws = useWorkspace();
  const now = useNow();
  const workspace = ws.workspace ?? EMPTY;
  const config = getConfiguration(workspace, configId);
  const robots = useMemo(() => config ? robotsFor(workspace, config.id) : [], [workspace, config]);
  const { views } = useRobotViews(workspace, robots, now);
  if (ws.status === "loading") return <AppShell crumbs={[ROOT, { label: "Configuration" }]}><LoadingState /></AppShell>;
  if (!config) return <AppShell crumbs={[ROOT, { label: configId }]}><NotFoundState title="Configuration not found." href={routes.index()} linkLabel="All configurations" /></AppShell>;
  return <Dashboard key={config.id} config={config} views={views} />;
}

function Dashboard({ config, views }: { config: Configuration; views: RobotView[] }) {
  const ws = useWorkspace();
  const router = useRouter();
  const tabs: TabItem[] = [{ id: "robots", label: "Robots", count: views.length }, { id: "details", label: "Details" }];
  const [tab, setTab] = useQueryTab(tabs);
  const [dialog, setDialog] = useState<"add" | "delete" | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const editable = ws.source === "document" && ws.canSave;
  const revision = currentRevision(config);
  const preview = robotPreview(revision.robot);
  const runs = views.flatMap(view => view.runs);
  const latest = latestScored(runs);
  const newest = runs.toSorted((a, b) => (Date.parse(b.at ?? "") || 0) - (Date.parse(a.at ?? "") || 0))[0] ?? null;
  const share = latest ? runShare(latest) : null;
  const live = views.find(view => view.robot.deviceId) ?? null;
  const attention = views.filter(view => view.status === "attention" || view.status === "degraded");
  const mode = revision.edgeHardware.powerModes.find(item => item.id === revision.edgeHardware.powerModeId);
  const count = (status: RobotView["status"]) => views.filter(view => view.status === status).length;
  // Live devices report online; simulators report whether an eval is running.
  const robotsSub = !views.length ? "None yet" : live ? `${count("online")} online` : count("running") ? `${count("running")} running` : "Idle";

  async function remove() {
    setBusy(true); setError(null);
    const result = await ws.save(current => deleteConfiguration(current, config.id, Date.now()));
    setBusy(false);
    if (result.ok) router.push(routes.index()); else setError(result.error);
  }

  const columns: Array<Column<RobotView>> = [
    { key: "name", header: "Robot", cell: view => view.robot.name },
    { key: "type", header: "Type", wide: true, cell: view => robotType(view.robot) },
    { key: "status", header: "Status", cell: view => <StatusBadge status={view.status} /> },
    { key: "evals", header: "Evals", numeric: true, wide: true, cell: view => view.runsLoading ? <Missing label="Loading" /> : fmtCount(view.runs.length) },
    { key: "last", header: "Last eval", cell: view => view.runs[0] ? <ResultBadge result={view.runs[0].result} progress={view.runs[0].progress} /> : <Missing label="No evals" /> },
  ];

  return <AppShell crumbs={[ROOT, { label: config.name }]}>
    <PageHeader title={config.name} badges={<ConfigStatusBadge status={config.status} />}
      actions={editable && <button className="cv-btn cv-btn--primary" type="button" onClick={() => { setAdded(null); setDialog("add"); }}>Add robot</button>} />
    <WorkspaceNotice />
    {ws.source === "document" && ws.documentRevision !== null && <WorkspaceConfigurationLink configurationId={config.id} documentRevision={ws.documentRevision} saving={ws.saving} />}
    {attention.length > 0 && <Notice tone="warning">{attention.length === 1 ? `${attention[0].robot.name} needs attention.` : `${attention.length} robots need attention.`}</Notice>}
    {added && <Notice action={<button className="cv-link" type="button" onClick={() => setAdded(null)}>Dismiss</button>}>{added} added.</Notice>}
    <Tiles label="Summary">
      <Tile label="Robots" value={fmtCount(views.length)} sub={robotsSub} />
      <Tile label="Evals" value={fmtCount(runs.length)} sub={newest?.at ? `Latest ${fmtDate(newest.at)}` : "None yet"} />
      <Tile label="Success rate" value={share === null ? null : fmtFixed(share * 100, 0)} unit="%" sub={latest ? `${latest.label} · ${latest.successes}/${latest.episodes}` : "No evals"} />
      <InferenceTile view={live} />
    </Tiles>
    {live && <section className="cv-row" aria-label={`${live.robot.name} telemetry`}>
      <div className="cv-row__head"><h2>{live.robot.name}</h2><StatusBadge status={live.status} />{live.readings.lastSeenAt && <time className="cv-muted" dateTime={live.readings.lastSeenAt}>{fmtWhen(live.readings.lastSeenAt)} UTC</time>}</div>
      <Tiles>
        <CpuTile view={live} /><MemoryTile view={live} /><TemperatureTile view={live} /><PowerTile view={live} />
      </Tiles>
    </section>}
    <Tabs tabs={tabs} value={tab} onChange={setTab} label="Configuration views" idPrefix="cd" />
    <TabPanel idPrefix="cd" tabId="robots" selected={tab === "robots"}>
      <Card label="Robots" flush>
        <DataTable label="Robots" columns={columns} rows={views} rowKey={view => view.robot.id} rowHref={view => routes.robot(config.id, view.robot.id)}
          empty={editable ? "No robots yet. Add one to start." : "No robots."} />
      </Card>
    </TabPanel>
    <TabPanel idPrefix="cd" tabId="details" selected={tab === "details"}>
      <Card label="Specification">
        <Facts items={[
          { label: "Robot", value: revision.robot.name },
          { label: "Edge hardware", value: revision.edgeHardware.name },
          { label: "Power mode", value: mode?.label ?? null },
          { label: "Edge model", value: models(revision.edgeModels) || null },
          { label: "Cloud model", value: models(revision.cloudModels) || null },
          { label: "Route", value: ROUTE_LABEL[revision.routing.mode] },
          { label: "Revision", value: revision.rev },
          { label: "Created", value: fmtDate(config.createdAt) },
        ]} />
      </Card>
      {editable && <div className="cv-danger"><button className="cv-btn cv-btn--danger" type="button" onClick={() => { setError(null); setDialog("delete"); }}>Delete configuration</button></div>}
    </TabPanel>
    {preview && <RobotSection config={config} revision={revision} preview={preview} />}
    {dialog === "add" && <AddRobotDialog config={config} onClose={() => setDialog(null)} onAdded={name => { setDialog(null); setAdded(name); }} />}
    {dialog === "delete" && <ConfirmDialog title={`Delete ${config.name}?`} action="Delete" busyAction="Deleting…" busy={busy} error={error} onConfirm={() => void remove()} onClose={() => setDialog(null)} />}
  </AppShell>;
}

/** The simulated robot turning (the wider side) beside the configuration's robot, edge hardware, models and status. */
function RobotSection({ config, revision, preview }: { config: Configuration; revision: ConfigRevision; preview: RobotPreview }) {
  const robot = revision.robot;
  return <section className="cv-row" aria-labelledby="cd-robot">
    <div className="cv-row__head"><h2 id="cd-robot">Robot</h2></div>
    <div className="cv-robot">
      <Turntable preview={preview} />
      <Card>
        <Facts single items={[
          { label: "Robot", value: robot.name },
          { label: "Body", value: robot.summary === NOT_SPECIFIED ? null : robot.summary },
          { label: "Cameras", value: robot.cameras.join(", ") || null },
          { label: "Edge hardware", value: revision.edgeHardware.name },
          { label: "Edge model", value: models(revision.edgeModels, true) || null },
          { label: "Cloud model", value: models(revision.cloudModels, true) || null },
          { label: "Status", value: <ConfigStatusBadge status={config.status} /> },
        ]} />
      </Card>
    </div>
  </section>;
}
