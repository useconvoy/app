"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { getRevision, getTrace, resolveRobotRoute, robotReadings, runsFor, tracesFor, useWorkspace } from "@/lib/configurations/client";
import { fmtDateTime, fmtUpdated, runLabel } from "@/lib/configurations/format";
import { clockText, displayHealth, promotionState } from "@/lib/configurations/robot";
import { routes } from "@/lib/configurations/routes";
import type { Configuration, ConvoyWorkspace, EvalRun, Robot, RobotRole } from "@/lib/configurations/types";
import { AppShell, type Crumb } from "../AppShell";
import { HealthBadge, ProvenanceBadge, RoleBadge } from "../Badges";
import { useNow, useQueryState } from "../hooks";
import { Icon } from "../Icons";
import { useLiveRefresh, useLiveRobot } from "../LiveDeviceProvider";
import { Notice, WorkspaceNotice, WorkspaceSourceNotice } from "../Notice";
import { PageHeader } from "../PageHeader";
import { LoadingState, NotFoundState } from "../States";
import { TabPanel, Tabs, useQueryTab, type TabItem } from "../Tabs";
import { ClearFlagDialog, FlagDialog, RoleDialog, RunEvaluationDialog, type SavedChange } from "./robot/Dialogs";
import { DeviceOverview, RobotFacts, RobotOverview } from "./robot/Overview";
import { ActionsPanel, InferencePanel, LogsPanel, RunsPanel } from "./robot/Panels";
import { TraceDrawer } from "./robot/TraceDrawer";

/**
 * Screen 3 · Robot dashboard (`/app/configurations/[configId]/robots/[robotId]`).
 * Test robots: Evals & sims (default) · Traces · Logs, with Run evaluation and a
 * gated Promote to production. A robot bound to a live device adds the device
 * hero, measured health and edge inference latency, and its Traces tab lists the
 * device's own gateway spans. Production robots: Actions & traces (default) · Logs,
 * with filter chips and the `?trace=` drawer. Tabs live in `?tab=`.
 */
export function RobotPage({ configId, robotId }: { configId: string; robotId: string }) {
  const ws = useWorkspace();
  const root: Crumb = { label: "Configurations", href: routes.index() };
  if (ws.status === "loading") return <AppShell crumbs={[root, { label: "Robot" }]}><LoadingState /></AppShell>;
  const route = resolveRobotRoute(ws.workspace, configId, robotId);
  if (!route) {
    return <AppShell crumbs={[root, { label: robotId }]}>
      <NotFoundState title="This robot is not in this configuration." text={`No robot “${robotId}” is attached to “${configId}”.`} href={ws.workspace.configurations.some(item => item.id === configId) ? routes.configuration(configId) : routes.index()} linkLabel="Back to the configuration" />
    </AppShell>;
  }
  return <RobotDashboard key={route.robot.id} workspace={ws.workspace} configuration={route.configuration} robot={route.robot} />;
}

type DialogState = { kind: "flag" } | { kind: "clear" } | { kind: "run" } | { kind: "role"; to: RobotRole; gateRun: EvalRun | null };
const NOTE_ID = "rb-actions-note";

function RobotDashboard({ workspace, configuration, robot }: { workspace: ConvoyWorkspace; configuration: Configuration; robot: Robot }) {
  const ws = useWorkspace();
  const now = useNow();
  const live = useLiveRobot(robot);
  const refresh = useLiveRefresh();
  const [dialog, setDialog] = useState<DialogState | null>(null);
  const [change, setChange] = useState<SavedChange | null>(null);
  const changeRef = useRef<HTMLDivElement>(null);

  const test = robot.role === "test";
  const bound = !!robot.deviceId;
  const revision = getRevision(configuration, robot.rev);
  const readings = robotReadings(robot, revision, live, now);
  const health = displayHealth(readings);
  const runs = runsFor(workspace, { robotId: robot.id });
  const traces = tracesFor(workspace, robot.id);
  const tabs: TabItem[] = test
    ? [{ id: "evals", label: "Evals & sims", count: runs.length }, { id: "traces", label: "Traces", count: bound ? live.data?.inference.length ?? null : traces.length }, { id: "logs", label: "Logs" }]
    : [{ id: "actions", label: "Actions & traces", count: traces.length }, { id: "logs", label: "Logs" }];
  const [tab, setTab] = useQueryTab(tabs);

  /* Trace drawer in `?trace=`: opening pushes (Back closes it); closing goes back when we pushed, else replaces. */
  const [traceId, setTraceId] = useQueryState("trace");
  const pushedTrace = useRef<string | null>(null);
  const lastTrace = useRef<string | null>(traceId);
  const found = traceId ? getTrace(workspace, traceId) : null;
  const trace = found && found.robotId === robot.id ? found : null;
  function openTrace(id: string) { pushedTrace.current = id; setTraceId(id); }
  function closeTrace() {
    if (traceId !== null && pushedTrace.current === traceId) { pushedTrace.current = null; window.history.back(); }
    else setTraceId(null, { replace: true });
  }
  // A drawer opened from the URL has no opener to return focus to: use its row's "View trace" button.
  useEffect(() => {
    const previous = lastTrace.current;
    lastTrace.current = traceId;
    if (!previous || traceId) return;
    const active = document.activeElement;
    if (active && active !== document.body) return;
    document.querySelector<HTMLElement>(`[data-trace-button="${CSS.escape(previous)}"]`)?.focus();
  }, [traceId]);
  // After a role change the button that opened the dialog is gone: move focus to the result.
  useEffect(() => { if (change?.moveFocus) changeRef.current?.focus(); }, [change]);

  function saved(next: SavedChange) { setDialog(null); setChange(next); }
  function queued(run: EvalRun) {
    setDialog(null);
    setChange({
      text: <><strong>{runLabel(run)}</strong> is queued on {robot.name} in the workspace document. No evaluation runner is connected, so it has not started.</>,
      action: <Link className="cfg-btn-text" href={routes.run(configuration.id, robot.id, run.id)}>Open {runLabel(run)}</Link>,
    });
    if (tab !== "evals") setTab("evals");
  }

  /* Header actions: one primary (Run evaluation) on test robots; reasons for unavailable actions sit under the buttons. */
  const saveBlocked = ws.canSave ? null : "Changes are not saved while the stored workspace cannot be read.";
  const promotion = promotionState(workspace, robot, configuration);
  const promoteBlocked = saveBlocked ?? (promotion.kind === "blocked" ? promotion.reason : null);
  const notes = [test && promotion.kind === "blocked" ? promotion.reason : null, saveBlocked].filter(Boolean);
  const blocked = (reason: string | null) => reason ? { "aria-disabled": true as const, "aria-describedby": NOTE_ID } : {};
  const when = (reason: string | null, next: DialogState) => () => { if (!reason) setDialog(next); };
  const actions = <div className="rb-actions">
    <div className="cfg-actions">
      <button className="cfg-btn-text" type="button" {...blocked(saveBlocked)} onClick={when(saveBlocked, { kind: "flag" })}><Icon name="flag" />Flag</button>
      {robot.flags.length > 0 && <button className="cfg-btn-text" type="button" {...blocked(saveBlocked)} onClick={when(saveBlocked, { kind: "clear" })}>Clear flag</button>}
      {test ? <>
        <button className="btn btn-secondary cfg-btn" type="button" {...blocked(promoteBlocked)}
          onClick={when(promoteBlocked, { kind: "role", to: "production", gateRun: promotion.kind === "allowed" ? promotion.run : null })}>Promote to production</button>
        <button className="btn btn-primary cfg-btn" type="button" {...blocked(saveBlocked)} onClick={when(saveBlocked, { kind: "run" })}>Run evaluation</button>
      </> : <button className="btn btn-secondary cfg-btn" type="button" {...blocked(saveBlocked)} onClick={when(saveBlocked, { kind: "role", to: "test", gateRun: null })}>Move to test</button>}
    </div>
    {notes.length > 0 && <p className="rb-actions-note" id={NOTE_ID}>{notes.join(" ")}</p>}
  </div>;

  const lede = robot.description ?? [
    `${revision?.robot.name ?? "Robot"} at ${robot.site}, running ${configuration.name}${robot.rev ? ` ${robot.rev}` : ""}.`,
    robot.clock ? `Times on this page use the robot’s device clock, ${clockText(robot.clock)}.` : "Times on this page are in UTC.",
  ].join(" ");
  const meta = bound
    ? live.data ? `${fmtUpdated(live.data.fetchedAt, 15)}${live.status === "stale" ? " · Update is stale" : ""}` : "Waiting for the device · Refreshes every 15 seconds"
    : fmtUpdated(ws.documentUpdatedAt ?? workspace.meta.updatedAt, null);
  const facts = <RobotFacts workspace={workspace} robot={robot} configuration={configuration} revision={revision} live={live} now={now} />;

  return <AppShell crumbs={[{ label: "Configurations", href: routes.index() }, { label: configuration.name, href: routes.configuration(configuration.id) }, { label: robot.name }]} context={<RoleBadge role={robot.role} />}>
    <PageHeader eyebrow={test ? "Test robot" : "Production robot"} title={robot.name} lede={lede} meta={meta} actions={actions}
      badges={<><RoleBadge role={robot.role} /><HealthBadge health={health.health} />{readings.provenance.kind !== "not-reported" && <ProvenanceBadge provenance={readings.provenance} now={now} />}</>} />
    <WorkspaceSourceNotice />
    <WorkspaceNotice workspace={workspace} configId={configuration.id} live={{ [robot.id]: live }} />
    {change && <div ref={changeRef} tabIndex={-1} className="rb-change">
      <Notice tone="info" icon="check" action={<>{change.action}<button className="cfg-btn-text" type="button" onClick={() => setChange(null)}>Dismiss</button></>}>{change.text}</Notice>
    </div>}
    {readings.flags.length > 0 && <Notice tone="warning" icon="flag">
      <strong>{readings.flags.length === 1 ? "1 flag in effect." : `${readings.flags.length} flags in effect.`}</strong>
      {readings.flags.map(flag => <span className="rb-flag rb-prov" key={flag.id}>
        <strong>{flag.label}</strong> · {flag.detail} <ProvenanceBadge provenance={flag.provenance} now={now} />
        <span className="rb-flag__meta">{flag.origin === "rule" ? "Flag rule on measured telemetry" : flag.by ? `Flagged by ${flag.by}` : flag.rule === "manual" ? "Manual flag" : "Stored flag"} · {fmtDateTime(flag.at, robot.clock)}</span>
      </span>)}
    </Notice>}

    {bound
      ? <DeviceOverview robot={robot} configuration={configuration} revision={revision} live={live} readings={readings} now={now} onRefresh={refresh} />
      : <RobotOverview robot={robot} configuration={configuration} revision={revision} readings={readings} now={now} />}
    {test && facts}

    <section className="cfg-section rb-views" aria-label={`${robot.name} views`}>
      <Tabs tabs={tabs} value={tab} onChange={setTab} label="Robot views" idPrefix="rb" />
      {test ? <>
        <TabPanel idPrefix="rb" tabId="evals" selected={tab === "evals"}><RunsPanel workspace={workspace} robot={robot} configurationId={configuration.id} now={now} /></TabPanel>
        <TabPanel idPrefix="rb" tabId="traces" selected={tab === "traces"}>
          {bound ? <InferencePanel data={live.data} robot={robot} now={now} /> : <ActionsPanel workspace={workspace} robot={robot} now={now} openTraceId={traceId} onOpen={openTrace} />}
        </TabPanel>
        <TabPanel idPrefix="rb" tabId="logs" selected={tab === "logs"}><LogsPanel workspace={workspace} robot={robot} now={now} /></TabPanel>
      </> : <>
        <TabPanel idPrefix="rb" tabId="actions" selected={tab === "actions"}><ActionsPanel workspace={workspace} robot={robot} now={now} openTraceId={traceId} onOpen={openTrace} /></TabPanel>
        <TabPanel idPrefix="rb" tabId="logs" selected={tab === "logs"}><LogsPanel workspace={workspace} robot={robot} now={now} /></TabPanel>
      </>}
    </section>
    {!test && facts}

    <TraceDrawer traceId={traceId} trace={trace} robot={robot} revision={revision} configurationName={configuration.name} now={now} onClose={closeTrace} />
    {dialog?.kind === "flag" && <FlagDialog robot={robot} onClose={() => setDialog(null)} onSaved={saved} />}
    {dialog?.kind === "clear" && <ClearFlagDialog robot={robot} onClose={() => setDialog(null)} onSaved={saved} />}
    {dialog?.kind === "role" && <RoleDialog robot={robot} configuration={configuration} to={dialog.to} gateRun={dialog.gateRun} onClose={() => setDialog(null)} onSaved={saved} />}
    {dialog?.kind === "run" && <RunEvaluationDialog workspace={workspace} configuration={configuration} robot={robot} onClose={() => setDialog(null)} onQueued={queued} />}
  </AppShell>;
}
