"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { attentionRobots, currentRevision, getConfiguration, getRevision, robotsFor, runHref, successShare, useWorkspace } from "@/lib/configurations/client";
import { ALL_LOGS, attentionSummary, DEFAULT_ROBOT_SORT, parseRobotFilter, revisionGate, robotRows } from "@/lib/configurations/dashboard";
import type { LogFilter, RobotFilter, RobotSort } from "@/lib/configurations/dashboard";
import { fmtCount, fmtDateTime, fmtShare, runLabel } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import type { Configuration, ConvoyWorkspace } from "@/lib/configurations/types";
import { AppShell, type Crumb } from "../AppShell";
import { ConfigStatusBadges, ProvenanceBadge } from "../Badges";
import { useNow, useQueryState } from "../hooks";
import { Icon } from "../Icons";
import { useLiveRefresh, useLiveRobots } from "../LiveDeviceProvider";
import { Notice, WorkspaceNotice } from "../Notice";
import type { NoticeTone } from "../Notice";
import { PageHeader } from "../PageHeader";
import { useSession } from "../Session";
import { LoadingState, NotFoundState } from "../States";
import { TabPanel, Tabs, useQueryTab } from "../Tabs";
import type { TabItem } from "../Tabs";
import { AddRobotDialog } from "./dashboard/AddRobotDialog";
import { FlagDialog, PromoteDialog, RunEvalDialog } from "./dashboard/Dialogs";
import { LogsFull, LogsPreview } from "./dashboard/Logs";
import { ConnectedDevice, LatencyPanel, ProductionKpis, TelemetryMultiples } from "./dashboard/Overview";
import { RobotsPanel } from "./dashboard/RobotsTable";
import { SpecificationFull, SpecificationSummary } from "./dashboard/Specification";

type TabId = "overview" | "robots" | "logs" | "spec";
type Dialog = { kind: "add" } | { kind: "run" } | { kind: "promote"; rev: string } | { kind: "flag"; robotId: string } | null;
/** A confirmed result, shown for the configuration it belongs to. */
interface Flash { configId: string; tone: NoticeTone; content: ReactNode }
const PREFIX = "cd";

/**
 * "2 robots need attention: Unit 08 … / 1 warning: Unit 13 …", each robot linked to its page. The
 * robots are those whose displayed health is Needs attention or Degraded (`attentionRobots`).
 */
function AttentionBanner({ workspace, config, live, now }: { workspace: ConvoyWorkspace; config: Configuration; live: Parameters<typeof attentionRobots>[2]; now: number | null }) {
  const { attention, warning } = attentionSummary(attentionRobots(workspace, config.id, live, now));
  if (!attention.length && !warning.length) return null;
  const lines = (items: typeof attention) => items.map((line, i) => <Fragment key={line.robot.id}>
    {i > 0 && <br />}<Link href={routes.robot(config.id, line.robot.id)}>{line.robot.name}</Link> {line.detail.replace(/\.$/, "")}{line.more ? ` (and ${line.more} more)` : ""}.{line.provenance.kind !== "not-reported" && <> <ProvenanceBadge provenance={line.provenance} now={now} /></>}
  </Fragment>);
  return <div className="cd-banner"><Notice tone="warning" icon="flag"
    action={attention.length > 0 ? <Link className="btn btn-secondary cfg-btn" href={`${routes.configuration(config.id, "robots")}&robots=attention`}>Show in robots table</Link> : undefined}>
    {attention.length > 0 && <><strong>{fmtCount(attention.length)} {attention.length === 1 ? "robot needs" : "robots need"} attention:</strong> {lines(attention)}</>}
    {attention.length > 0 && warning.length > 0 && <br />}
    {warning.length > 0 && <><strong>{fmtCount(warning.length)} {warning.length === 1 ? "warning" : "warnings"}:</strong> {lines(warning)}</>}
  </Notice></div>;
}

/** The candidate revision's latest gate decision; a pass offers promotion. */
function GateNotice({ workspace, config, canSave, onPromote }: { workspace: ConvoyWorkspace; config: Configuration; canSave: boolean; onPromote: (rev: string) => void }) {
  const rev = config.candidateRev;
  if (!rev) return null;
  const gate = revisionGate(workspace, config, rev);
  const run = gate.run;
  if (!run) return <Notice tone="info">{rev} has no gated suite run yet. Run the eval suite to check it against the promotion gate.</Notice>;
  const href = runHref(workspace, run);
  const safety = run.safety ? `${fmtCount(run.safety.critical)} critical safety ${run.safety.critical === 1 ? "event" : "events"}` : "safety not reported";
  if (gate.passed) {
    return <Notice tone="success" action={<button className="btn btn-secondary cfg-btn" type="button" aria-disabled={!canSave || undefined} onClick={() => { if (canSave) onPromote(rev); }}>Promote {rev} to production</button>}>
      <strong>{rev} passed gate on {href ? <Link href={href}>{runLabel(run)}</Link> : runLabel(run)}:</strong> {fmtShare(successShare(run))} success, n = {fmtCount(run.counts.episodes)} · {safety} <ProvenanceBadge provenance={run.provenance} />
    </Notice>;
  }
  const failing = gate.lines.filter(line => !line.passed).map(line => `${line.label}: ${line.actual}${line.target ? ` (needs ${line.target})` : ""}`);
  return <Notice tone="warning" icon="blocked">
    <strong>{rev} is below gate on {href ? <Link href={href}>{runLabel(run)}</Link> : runLabel(run)}:</strong> {failing.length ? failing.join(" · ") : "the gate did not pass"}. Production robots cannot move to {rev} until a run passes. <ProvenanceBadge provenance={run.provenance} />
  </Notice>;
}

/**
 * Screen 2 · Configuration dashboard (`/app/configurations/[configId]`).
 * Tabs in `?tab=` (Overview · Robots · Logs · Specification); the robots filter in
 * `?robots=`. Every write (add robot, flag, promote, queue a run) goes through the
 * workspace document's `save()`; a success notice appears only after it is confirmed.
 */
export function ConfigDashboardPage({ configId }: { configId: string }) {
  const ws = useWorkspace();
  const session = useSession();
  const now = useNow();
  const workspace = ws.workspace;
  const configuration = workspace ? getConfiguration(workspace, configId) : null;
  const robots = useMemo(() => workspace && configuration ? robotsFor(workspace, configuration.id) : [], [workspace, configuration]);
  const live = useLiveRobots(robots);
  const refresh = useLiveRefresh();
  const tabs: TabItem[] = [{ id: "overview", label: "Overview" }, { id: "robots", label: "Robots", count: robots.length }, { id: "logs", label: "Logs" }, { id: "spec", label: "Specification" }];
  const [tab, setTab] = useQueryTab(tabs);
  const [filterParam, setFilterParam] = useQueryState("robots");
  const filter = parseRobotFilter(filterParam);
  const [sort, setSort] = useState<RobotSort>(DEFAULT_ROBOT_SORT);
  const [logFilter, setLogFilter] = useState<LogFilter>(ALL_LOGS);
  const [specRev, setSpecRev] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const flashRef = useRef<HTMLDivElement>(null);
  // After a dialog closes on success its opener may be gone (e.g. the promote button): put focus on the result instead of the page body.
  useEffect(() => {
    if (flash && (document.activeElement === document.body || !document.activeElement)) flashRef.current?.focus();
  }, [flash]);

  const root: Crumb = { label: "Configurations", href: routes.index() };
  if (ws.status === "loading" || !workspace) return <AppShell crumbs={[root, { label: "Configuration" }]}><LoadingState /></AppShell>;
  if (!configuration) {
    return <AppShell crumbs={[root, { label: configId }]}>
      <NotFoundState title="This configuration is not in the workspace." text={`No configuration has the id “${configId}”. It may have been removed or renamed.`} href={routes.index()} linkLabel="Back to configurations" />
    </AppShell>;
  }

  const rows = robotRows(workspace, configuration, live, now);
  const revision = currentRevision(configuration);
  const shownRev = (specRev && getRevision(configuration, specRev)) || revision;
  const counts = { test: rows.filter(row => row.robot.role === "test").length, production: rows.filter(row => row.robot.role === "production").length };
  const bound = rows.filter(row => row.bound);
  const meta = `${fmtCount(rows.length)} ${rows.length === 1 ? "robot" : "robots"} · Test ${fmtCount(counts.test)} · Production ${fmtCount(counts.production)} · Updated ${fmtDateTime(configuration.updatedAt)}${bound.length ? " · Device data refreshes every 15 seconds" : ""}`;
  const closeDialog = () => setDialog(null);
  const done = (content: ReactNode) => { setDialog(null); setFlash({ configId: configuration.id, tone: "success", content }); };
  const openTab = (id: TabId) => {
    setTab(id);
    window.setTimeout(() => document.getElementById(`${PREFIX}-tab-${id}`)?.focus(), 0);
  };
  const setFilter = (next: RobotFilter) => setFilterParam(next === "all" ? null : next, { replace: true });
  const flagRow = dialog?.kind === "flag" ? rows.find(row => row.robot.id === dialog.robotId) ?? null : null;
  const robotsPanel = (manage: boolean) => <RobotsPanel config={configuration} rows={rows} filter={filter} onFilter={setFilter} sort={sort} onSort={setSort} now={now}
    onFlag={manage ? robotId => setDialog({ kind: "flag", robotId }) : undefined} onAdd={() => setDialog({ kind: "add" })} titleId={manage ? "cd-robots-tab-title" : "cd-robots-title"} />;

  return <AppShell crumbs={[root, { label: configuration.name }]}>
    <PageHeader eyebrow="Configuration" title={configuration.name} badges={<ConfigStatusBadges configuration={configuration} />}
      actions={<>
        <Link className="btn btn-secondary cfg-btn" href={routes.newConfiguration(configuration.id)}>Edit configuration</Link>
        <button className="btn btn-secondary cfg-btn" type="button" onClick={() => setDialog({ kind: "run" })}>Run eval suite</button>
        <button className="btn btn-primary cfg-btn" type="button" onClick={() => setDialog({ kind: "add" })}>Add robot <Icon name="plus" /></button>
      </>}
      meta={meta} />
    <WorkspaceNotice workspace={workspace} configId={configuration.id} />
    {flash?.configId === configuration.id && <div ref={flashRef} tabIndex={-1} className="cd-flash">
      <Notice tone={flash.tone} action={<button className="cfg-btn-text" type="button" onClick={() => setFlash(null)}>Dismiss</button>}>{flash.content}</Notice>
    </div>}
    <AttentionBanner workspace={workspace} config={configuration} live={live} now={now} />
    <GateNotice workspace={workspace} config={configuration} canSave={ws.canSave} onPromote={rev => setDialog({ kind: "promote", rev })} />

    <Tabs tabs={tabs} value={tab} onChange={setTab} label="Configuration views" idPrefix={PREFIX} />
    <TabPanel idPrefix={PREFIX} tabId="overview" selected={tab === "overview"}>{tab === "overview" && <>
      <ProductionKpis workspace={workspace} config={configuration} rows={rows} now={now} />
      {bound.map(row => <ConnectedDevice key={row.robot.id} config={configuration} row={row} binding={live[row.robot.id]} now={now} onRefresh={() => void refresh()} />)}
      <TelemetryMultiples config={configuration} rows={rows} now={now} />
      <LatencyPanel config={configuration} rows={rows} now={now} />
      {robotsPanel(false)}
      <SpecificationSummary workspace={workspace} config={configuration} revision={revision} rows={rows} live={live} onViewAll={() => openTab("spec")} />
      <LogsPreview workspace={workspace} config={configuration} onOpen={() => openTab("logs")} />
    </>}</TabPanel>
    <TabPanel idPrefix={PREFIX} tabId="robots" selected={tab === "robots"}>{tab === "robots" && <>
      {robotsPanel(true)}
      <UnattachedNote workspace={workspace} onAdd={() => setDialog({ kind: "add" })} />
    </>}</TabPanel>
    <TabPanel idPrefix={PREFIX} tabId="logs" selected={tab === "logs"}>{tab === "logs" && <LogsFull workspace={workspace} config={configuration} filter={logFilter} onFilter={setLogFilter} />}</TabPanel>
    <TabPanel idPrefix={PREFIX} tabId="spec" selected={tab === "spec"}>{tab === "spec" && <SpecificationFull workspace={workspace} config={configuration} revision={shownRev} rows={rows} live={live} onRevision={setSpecRev} now={now} />}</TabPanel>

    {dialog?.kind === "add" && <AddRobotDialog workspace={workspace} config={configuration} canSave={ws.canSave} save={ws.save} onClose={closeDialog}
      onDone={({ robot, role, rev }) => done(<>{robot.name} added to {configuration.name} {rev} as {role === "test" ? "a test robot" : "a production robot"}. <Link href={routes.robot(configuration.id, robot.id)}>Open {robot.name}</Link></>)} />}
    {dialog?.kind === "run" && <RunEvalDialog workspace={workspace} config={configuration} rows={rows} canSave={ws.canSave} save={ws.save} onClose={closeDialog}
      onDone={run => {
        const href = run && ws.workspace ? runHref(ws.workspace, run) : null;
        done(<>{run ? `${runLabel(run)} queued for ${run.rev}` : "Run queued"}. No evaluation runner is connected to this workspace, so it stays queued until a runner reports results.{run && href && <> <Link href={href}>Open {runLabel(run)}</Link></>}</>);
      }} />}
    {dialog?.kind === "promote" && <PromoteDialog workspace={workspace} config={configuration} rev={dialog.rev} canSave={ws.canSave} save={ws.save} onClose={closeDialog}
      onDone={({ rev, from, robots: moved }) => done(<>{rev} is now the production revision of {configuration.name}{from ? ` (was ${from})` : ""}; {fmtCount(moved)} production {moved === 1 ? "robot is" : "robots are"} recorded on {rev}.</>)} />}
    {flagRow && <FlagDialog row={flagRow} canSave={ws.canSave} save={ws.save} by={session.email} now={now} onClose={closeDialog}
      onDone={label => done(<>{flagRow.robot.name} flagged: {label}.</>)} />}
  </AppShell>;
}

/** Robots tab: registered robots that no configuration uses, with the way to add one. */
function UnattachedNote({ workspace, onAdd }: { workspace: ConvoyWorkspace; onAdd: () => void }) {
  const unattached = workspace.robots.filter(robot => robot.configId === null);
  if (!unattached.length) return null;
  return <p className="portal-context-note cd-unattached">
    {unattached.map(robot => `${robot.name} (${robot.site})`).join(", ")} {unattached.length === 1 ? "is" : "are"} registered and not attached to a configuration.{" "}
    <button className="cfg-btn-text" type="button" onClick={onAdd}>Add robot</button>
  </p>;
}
