"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { currentRevision, latestGateRun, latencyRows, productionRevision, runHref, successShare } from "@/lib/configurations/client";
import {
  cadenceLabel, latencyTicks, niceTicks, productionKpis, seriesWindowLabel, sparkReferences, sparkZone, telemetryDomain, weakestProvenance,
} from "@/lib/configurations/dashboard";
import type { RobotRow } from "@/lib/configurations/dashboard";
import { deviceState } from "@/lib/configurations/live";
import type { DeviceState as SharedDeviceState, LiveBinding } from "@/lib/configurations/live";
import { fmtCi, fmtCount, fmtDateTime, fmtFixed, fmtMs, fmtNumber, fmtPct, fmtRange, fmtRelative, provenanceLabel, runLabel } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import { CONFIGURED_DEVICE } from "@/lib/configurations/types";
import type { ClockKind, Configuration, ConvoyWorkspace, LatencySeries, Provenance, SeriesPoint, TelemetryMetric } from "@/lib/configurations/types";
import { HealthBadge, ProvenanceBadge, RoleBadge, RunStatus } from "../../Badges";
import { BandPlot, Legend, PlotPair, sparkGeometry } from "../../Charts";
import type { LegendItem } from "../../Charts";
import { DeviceBoard, Panel } from "../../Facts";
import { Icon } from "../../Icons";
import { KpiGrid, KpiTile } from "../../Kpi";
import { Notice } from "../../Notice";

const NOT_REPORTED: Provenance = { kind: "not-reported" };
const CLOCK: Record<ClockKind, string> = { device: "device clock", browser: "browser clock", wall: "wall clock", simulated: "simulated clock", server: "server clock" };
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const plural = (count: number, one: string, many = `${one}s`) => `${fmtCount(count)} ${count === 1 ? one : many}`;
const names = (rows: readonly RobotRow[]) => rows.map(row => row.robot.name).join(", ");

/* ---------- production KPIs ---------- */

/**
 * Six tiles over the production robots (stored values only): reporting count,
 * edge and cloud latency, interventions and safety events from the production
 * summary (or the robots, with the basis stated), and the latest gated eval.
 */
export function ProductionKpis({ workspace, config, rows, now }: { workspace: ConvoyWorkspace; config: Configuration; rows: readonly RobotRow[]; now: number | null }) {
  const kpis = productionKpis(config, rows);
  const revision = productionRevision(config) ?? currentRevision(config);
  const window = kpis.window;
  const inWindow = window ? ` · ${window}` : "";
  const run = latestGateRun(workspace, config.id, config.candidateRev) ?? latestGateRun(workspace, config.id);
  const share = run ? successShare(run) : null;
  const href = run ? runHref(workspace, run) : null;
  const edge = kpis.edge, cloud = kpis.cloud;
  const noCloud = revision.routing.mode === "edge-only" && !cloud;
  const latencySub = (kpi: typeof edge, extra = ""): string => !kpi ? "Not in the production summary"
    : kpi.basis === "summary" ? `p95 ${fmtMs(kpi.p95)}${extra} · ${plural(kpi.n, "robot")}${inWindow}`
      : `Median of ${plural(kpi.n, "robot")}’ p50${kpi.p95Range ? ` · p95 ${fmtNumber(kpi.p95Range[0], 0)}–${fmtNumber(kpi.p95Range[1], 0)} ms` : ""}${extra}`;
  const fallback = kpis.fallback ? ` · fallback ${fmtPct(kpis.fallback.pct)} of chunks` : "";
  const interventions = kpis.interventions;
  const safety = kpis.safety;
  const note = kpis.reporting.of === 0 ? "No production robots" : `${provenanceLabel(kpis.provenance)}${window ? ` · last ${window}` : ""} · ${plural(kpis.reporting.of, "production robot")}`;
  const latestEval = <KpiTile label={run ? `Latest eval · ${run.rev} · ${run.title}` : "Latest eval"} value={share === null ? null : share * 100} digits={1} unit="%"
    sub={run ? `${fmtCount(run.counts.successes)} / ${fmtCount(run.counts.episodes)} episodes${run.successCi95 ? ` · 95 % CI ${fmtCi(run.successCi95)}` : ""}` : "No gated suite run yet"}
    provenance={run?.provenance ?? NOT_REPORTED} now={now}
    foot={run && <><RunStatus run={run} />{href && <Link className="cfg-btn-text cd-push" href={href}>Open {runLabel(run)} <Icon name="chevron-right" /></Link>}</>} />;
  // Nothing to aggregate: say so once instead of five "Not reported" tiles.
  if (kpis.reporting.of === 0 && !kpis.summary && !kpis.excluded.length) {
    return <section className="cfg-section" aria-labelledby="cd-kpis-title">
      <div className="portal-section-label"><h2 id="cd-kpis-title">Production KPIs</h2><span>{note}</span></div>
      <KpiGrid columns={3}>
        <div className="portal-metric-card cd-kpi-empty">
          <p className="cd-kpi-empty__title">No production robots are attached</p>
          <p className="cd-kpi-empty__text">Production figures appear here once production robots run a revision of this configuration that passed its gate.</p>
        </div>
        {latestEval}
      </KpiGrid>
    </section>;
  }
  return <section className="cfg-section" aria-labelledby="cd-kpis-title">
    <div className="portal-section-label"><h2 id="cd-kpis-title">Production KPIs</h2><span>{note}</span></div>
    <KpiGrid columns={3}>
      <KpiTile label="Robots reporting" value={kpis.reporting.count} digits={0} unit={`of ${fmtCount(kpis.reporting.of)}`}
        sub={kpis.reporting.of ? "Production robots with a current report" : "No production robots are attached"} provenance={kpis.reporting.provenance} now={now} />
      <KpiTile label="Edge planner p50" series="edge" value={edge?.p50 ?? null} digits={0} unit="ms" sub={latencySub(edge)} provenance={edge?.provenance ?? NOT_REPORTED} now={now} />
      <KpiTile label="Cloud policy chunk p50" series="cloud" value={cloud?.p50 ?? null} digits={0} unit="ms"
        sub={noCloud ? "No cloud model in this configuration" : latencySub(cloud, fallback)} provenance={cloud?.provenance ?? NOT_REPORTED} now={now} />
      <KpiTile label="Remote-operator interventions" value={interventions?.mean ?? null} digits={1} unit="per robot-hour"
        sub={interventions ? `Mean of ${plural(interventions.n, "robot")} · ${fmtNumber(interventions.min)}–${fmtNumber(interventions.max)} per robot${interventions.basis === "summary" ? inWindow : ""}` : "No intervention rates reported"}
        provenance={interventions?.provenance ?? NOT_REPORTED} now={now} />
      <KpiTile label={`Safety events${inWindow}`} value={safety?.critical ?? null} digits={0} unit="critical"
        sub={safety ? `${fmtCount(safety.major)} major · ${fmtCount(safety.minor)} minor${safety.note ? `, ${safety.note}` : ""} · ${plural(kpis.summary?.robots ?? kpis.reporting.of, "robot")}` : "No safety events in the production summary"}
        provenance={safety?.provenance ?? NOT_REPORTED} now={now} />
      {latestEval}
    </KpiGrid>
    {kpis.excluded.length > 0 && <p className="portal-context-note">{names(kpis.excluded)} {kpis.excluded.length === 1 ? "is" : "are"} not part of these figures: live-bound robots report measured values, shown under Connected device.</p>}
  </section>;
}

/* ---------- connected device ---------- */

type DeviceState = Exclude<SharedDeviceState, "unbound" | "signed-out">;
/** The shared device state (`deviceState` in live.ts, as in the page notice), with "no binding yet" read as waiting. */
function deviceStateOf(binding: LiveBinding | undefined): DeviceState {
  const state = deviceState(binding);
  return state === "unbound" ? "waiting" : state === "signed-out" ? "unavailable" : state;
}

/**
 * The live-bound robot: the running release on the teal device board, four measured
 * tiles with the sample time, and its stored (recorded) latency evidence apart.
 * Values read "Not reported" while the device is offline or cannot be read.
 */
export function ConnectedDevice({ config, row, binding, now, onRefresh }: { config: Configuration; row: RobotRow; binding: LiveBinding | undefined; now: number | null; onRefresh: () => void }) {
  const { robot, readings } = row;
  const data = binding?.data ?? null;
  const state = deviceStateOf(binding);
  const shows = state === "reporting" || state === "stale";
  const hardware = (row.revision ?? currentRevision(config)).edgeHardware;
  const mode = hardware.powerModes.find(item => item.id === hardware.powerModeId);
  const release = data?.release ?? null;
  const repo = [release?.modelFile, release?.runtime, release?.backend, data?.agentVersion ? `agent ${data.agentVersion}` : null].filter(Boolean).join(" · ");
  const measured = shows ? readings.provenance : NOT_REPORTED;
  const latest = row.current;
  const samples = readings.recent.socTempC?.length ?? 0;
  const titleId = `cd-device-${robot.id}`;
  const recorded = robot.latency && robot.latency.provenance.kind === "recorded" ? robot.latency : null;
  const chat = robot.deviceId === CONFIGURED_DEVICE || data?.source === "portal-snapshot";
  return <section className="cfg-section" aria-labelledby={titleId}>
    <div className="portal-section-label"><h2 id={titleId}>Connected device · {robot.name}</h2><span>{shows ? provenanceLabel(readings.provenance, now) : "Not reported"} · {robot.site}</span></div>
    <DeviceBoard eyebrow={`${robot.name} · ${hardware.name}`} title={release ? release.name : "Running release not reported"} repo={repo || undefined} drawingLabel="Edge device"
      facts={<><RoleBadge role={robot.role} /><HealthBadge health={readings.health} />{shows && <ProvenanceBadge provenance={measured} now={now} />}
        {row.href && <Link className="cfg-btn-text" href={row.href}>Open {robot.name} <Icon name="chevron-right" /></Link>}</>}
      action={chat ? <Link className="btn btn-secondary cfg-btn" href={routes.chat()}>Open chat <span aria-hidden="true">↗</span></Link> : undefined} />
    <KpiGrid className="cd-after">
      <KpiTile label="Planner p50" series="edge" value={row.edge.ms?.p50 ?? null} digits={0} unit="ms"
        sub={row.edge.ms ? `p95 ${fmtMs(row.edge.ms.p95)} · newest ${plural(row.edge.ms.n ?? 0, "request")}` : shows ? "No inference received from the device" : "Gateway latency on the device"} provenance={row.edge.provenance} now={now} />
      <KpiTile label="Jetson SoC temperature" value={finite(latest?.socTempC) ? fmtFixed(latest.socTempC) : null} unit="°C"
        sub={`Hottest zone${finite(hardware.thermal.swThrottleC) ? ` · software throttle at ${fmtNumber(hardware.thermal.swThrottleC)} °C` : ""}`} provenance={measured} now={now} />
      <KpiTile label="Board input power" value={finite(latest?.boardPowerW) ? fmtFixed(latest.boardPowerW) : null} unit="W"
        sub={`VDD_IN, total board input${mode ? ` · ${mode.label} mode` : ""}`} provenance={measured} now={now} />
      <KpiTile label="Memory available" value={finite(latest?.memAvailableMiB) ? fmtNumber(latest.memAvailableMiB, 0) : null} unit="MiB"
        sub={finite(latest?.memTotalMiB) ? `of ${fmtNumber(latest.memTotalMiB, 0)} MiB, shared CPU/GPU` : `of ${fmtNumber(hardware.memoryGiB)} GiB, shared CPU/GPU`} provenance={measured} now={now} />
    </KpiGrid>
    {shows && latest?.at && <p className="portal-context-note">Latest sample <time dateTime={latest.at}>{fmtDateTime(latest.at)}</time> on the {CLOCK.device} · {plural(samples, "sample")} received · read every 15 seconds while this page is open.</p>}
    <DeviceStatus state={state} row={row} binding={binding} now={now} onRefresh={onRefresh} />
    {recorded && <p className="portal-context-note cd-evidence"><ProvenanceBadge provenance={recorded.provenance} /> {recorded.window[0].toUpperCase()}{recorded.window.slice(1)} for {robot.name}: planner p50 {fmtMs(recorded.edgePlannerMs.p50)}, p95 {fmtMs(recorded.edgePlannerMs.p95)}{recorded.ttftMs ? `, time to first token p50 ${fmtMs(recorded.ttftMs.p50)}` : ""}{finite(recorded.edgePlannerMs.n) ? ` (n = ${fmtCount(recorded.edgePlannerMs.n)} requests)` : ""}. Stored evidence, not a live reading.</p>}
  </section>;
}

function DeviceStatus({ state, row, binding, now, onRefresh }: { state: DeviceState; row: RobotRow; binding: LiveBinding | undefined; now: number | null; onRefresh: () => void }) {
  const retry = <button className="btn btn-secondary cfg-btn" type="button" onClick={onRefresh}>Check again</button>;
  if (state === "waiting") return <p className="portal-context-note" role="status">Waiting for the first report from {row.robot.name}.</p>;
  if (state === "unavailable") return <Notice tone="warning" action={retry}>{row.robot.name} could not be read{binding?.error ? `: ${binding.error.replace(/\.$/, "")}` : ""}. Its values read Not reported until the device reports.</Notice>;
  if (state === "offline") {
    const seen = row.readings.lastSeenAt;
    return <Notice tone="warning" action={retry}>{row.robot.name} is offline ({row.readings.baseHealthReason ?? "no recent live contact"}). {seen ? <>Last contact <time dateTime={seen}>{fmtDateTime(seen)}</time>. </> : null}Current values read Not reported.</Notice>;
  }
  if (state === "stale") return <p className="portal-context-note" role="status">The last successful device read was {binding?.data ? fmtRelative(binding.data.fetchedAt, now) : "a while ago"}; the update is stale and Convoy keeps retrying.</p>;
  return null;
}

/* ---------- telemetry small multiples ---------- */

interface CardSpec { row: RobotRow; points: SeriesPoint[] }
const severityLabel = { attention: "Needs attention", warning: "Warning" } as const;

/** One robot's card: current value, flag marker and a sparkline on the group's domain with reference lines (and a guard band). */
function TelemetryCard({ spec, metric, unit, title, domain, references, zone, now }: {
  spec: CardSpec; metric: Extract<TelemetryMetric, "socTempC" | "boardPowerW">; unit: string; title: string; domain: [number, number];
  references: Array<number | null>; zone: [number | null, number | null] | null; now: number | null;
}) {
  const { row, points } = spec;
  const geometry = sparkGeometry(points, domain);
  const flag = row.readings.flags.find(item => item.rule === (metric === "socTempC" ? "soc-temp" : "board-power"));
  const value = row.current?.[metric] ?? null;
  const range = fmtRange(points.map(point => point.value), unit);
  const window = row.bound ? plural(geometry.count, "sample") : "24 h";
  const refText = references.filter(finite).map(item => `${fmtNumber(item)} ${unit}`).join(" and ");
  const label = `${row.robot.name} ${title}, ${row.bound ? `${plural(geometry.count, "measured sample")}` : `hourly ${metric === "socTempC" ? "max" : "peak"} over 24 h`}, ${range}${refText ? `; reference lines at ${refText}` : ""}`;
  return <article className={`cfg-mini${flag ? " cfg-mini--flag" : ""}`}>
    <div className="cfg-mini__head">{row.href ? <Link className="cfg-mini__name" href={row.href}>{row.robot.name}</Link> : <span className="cfg-mini__name">{row.robot.name}</span>}<RoleBadge role={row.robot.role} /></div>
    <p className="cfg-mini__value">{finite(value) ? <>{fmtFixed(value)}<span>{unit}</span></> : <span className="cfg-prov cfg-prov--none">Not reported</span>}
      {flag && <span className="cfg-mini__flag"><Icon name="flag" /><span className="cfg-sr">{severityLabel[flag.severity]}: {flag.label}</span></span>}</p>
    {geometry.count ? <figure className={`portal-chart cfg-spark${flag ? " cfg-series--flag" : ""}`}>
      <svg viewBox="0 0 300 80" role="img" aria-label={label}>
        {zone && <path className="cd-spark__zone" d={sparkZone(zone[0], zone[1], domain)} />}
        <path className="portal-chart-axis" d="M12 70H288" />
        <path className="cfg-spark__ref" d={sparkReferences(references, domain)} />
        <path className="portal-chart-line" d={geometry.line} />
        <path className="cfg-spark__dots" d={geometry.dots} />
      </svg>
      <figcaption>{range} · {window} <ProvenanceBadge provenance={row.readings.provenance} now={now} /></figcaption>
    </figure> : <p className="portal-chart-empty">{row.bound ? "No measured samples received yet." : `No ${metric === "socTempC" ? "SoC temperature" : "board input power"} reported.`}</p>}
  </article>;
}

/**
 * Two groups of small multiples, one card per robot: Jetson SoC temperature
 * (thermal guard and software throttle lines) and board input power (power-mode
 * cap). Each group shares one y-domain; a measured card plots received samples,
 * the others their stored hourly series, never in the same plot.
 */
export function TelemetryMultiples({ config, rows, now }: { config: Configuration; rows: readonly RobotRow[]; now: number | null }) {
  if (!rows.length) return null;
  const revision = currentRevision(config);
  const limits = (row: RobotRow) => {
    const hardware = (row.revision ?? revision).edgeHardware, routing = (row.revision ?? revision).routing;
    return { guard: routing.thermalGuard.socTempC, throttle: hardware.thermal.swThrottleC, cap: hardware.powerModes.find(item => item.id === hardware.powerModeId)?.capW ?? null };
  };
  const series = (row: RobotRow, metric: TelemetryMetric) => (row.bound ? row.readings.recent[metric] : row.readings.day[metric]) ?? [];
  const temps = rows.map(row => ({ row, points: series(row, "socTempC") }));
  const powers = rows.map(row => ({ row, points: series(row, "boardPowerW") }));
  const values = (specs: readonly CardSpec[]) => specs.flatMap(spec => spec.points.map(point => point.value));
  const shared = { guard: revision.routing.thermalGuard.socTempC, throttle: revision.edgeHardware.thermal.swThrottleC, cap: revision.edgeHardware.powerModes.find(item => item.id === revision.edgeHardware.powerModeId) };
  const tempDomain = telemetryDomain(values(temps), rows.flatMap(row => [limits(row).guard, limits(row).throttle])) ?? [30, 105];
  const powerDomain = telemetryDomain(values(powers), rows.map(row => limits(row).cap), { zero: true }) ?? [0, 30];
  const bound = rows.filter(row => row.bound), stored = rows.filter(row => !row.bound);
  const storedLabel = provenanceLabel(weakestProvenance(stored.map(row => row.readings.provenance)));
  const note = `Last 24 h · ${CLOCK.device}, UTC · one card per robot${bound.length ? ` · ${names(bound)}: Measured samples; others: ${storedLabel}` : ""}`;
  // No robot has a series to draw (e.g. only simulators): one sentence instead of empty cards.
  if (![...temps, ...powers].some(spec => spec.points.some(point => finite(point.value)))) {
    const simulators = rows.filter(row => row.robot.kind === "simulator").length;
    return <section className="cfg-section" aria-labelledby="cd-telemetry-title">
      <div className="portal-section-label"><h2 id="cd-telemetry-title">Robot telemetry</h2><span>One card per robot</span></div>
      <p className="portal-empty cd-empty-note">No robot in this configuration reports Jetson SoC temperature or board input power{simulators === rows.length ? ": simulators send no device telemetry" : bound.length ? " yet" : ""}.</p>
    </section>;
  }
  const measuredNote = bound.length ? "; measured cards plot received samples" : "";
  const tempLegend: LegendItem[] = [
    { key: "line", label: `Hourly max${measuredNote} · ${fmtNumber(tempDomain[0])}–${fmtNumber(tempDomain[1])} °C on every card` },
    ...(finite(shared.guard) || finite(shared.throttle) ? [{ key: "ref" as const, label: [finite(shared.guard) ? `Thermal guard ${fmtNumber(shared.guard)} °C` : null, finite(shared.throttle) ? `software throttle ${fmtNumber(shared.throttle)} °C` : null].filter(Boolean).join(" · ") }] : []),
    { key: "line", series: "flag", label: "Flagged" },
  ];
  const powerLegend: LegendItem[] = [
    { key: "line", label: `Hourly peak${measuredNote} · ${fmtNumber(powerDomain[0])}–${fmtNumber(powerDomain[1])} W on every card` },
    ...(shared.cap && finite(shared.cap.capW) ? [{ key: "ref" as const, label: `${shared.cap.label} power mode` }] : []),
    { key: "line", series: "flag", label: "Flagged" },
  ];
  return <section className="cfg-section" aria-labelledby="cd-telemetry-title">
    <div className="portal-section-label"><h2 id="cd-telemetry-title">Robot telemetry</h2><span>{note}</span></div>
    <div className="cfg-subhead"><h3>Jetson SoC temperature by robot</h3><Legend items={tempLegend} /></div>
    <div className="cfg-mini-grid cfg-mini-grid--4">
      {temps.map(spec => {
        const { guard, throttle } = limits(spec.row);
        return <TelemetryCard key={spec.row.robot.id} spec={spec} metric="socTempC" unit="°C" title="Jetson SoC temperature" domain={tempDomain} references={[guard, throttle]} zone={[guard, throttle]} now={now} />;
      })}
    </div>
    <div className="cfg-subhead cd-group"><h3>Board input power by robot</h3><Legend items={powerLegend} /></div>
    <div className="cfg-mini-grid cfg-mini-grid--4">
      {powers.map(spec => <TelemetryCard key={spec.row.robot.id} spec={spec} metric="boardPowerW" unit="W" title="Board input power" domain={powerDomain} references={[limits(spec.row).cap]} zone={null} now={now} />)}
    </div>
  </section>;
}

/* ---------- edge vs cloud latency ---------- */

function spanText(values: ReadonlyArray<number | null>): string {
  const known = values.filter(finite);
  return known.length ? `${fmtNumber(Math.min(...known), 0)}–${fmtNumber(Math.max(...known), 0)}` : "Not reported";
}

/**
 * The production summary's edge and cloud latency series as two aligned band plots
 * (p50 line, p50–p95 band) on the same time ticks, each with its own y-scale, the
 * edge budget and the fallback trigger as labelled reference lines, and hours with
 * heavy fallback marked along the top of the cloud plot.
 */
export function LatencyPanel({ config, rows, now }: { config: Configuration; rows: readonly RobotRow[]; now: number | null }) {
  const summary = config.production ?? null;
  const revision = productionRevision(config) ?? currentRevision(config);
  const edge = summary?.latency.edge ?? null, cloud = summary?.latency.cloud ?? null;
  const reference = edge ?? cloud;
  const bound = rows.filter(row => row.bound);
  const window = summary?.window.label ?? "24 h";
  const eyebrow = `Production robots · last ${window}${reference ? ` · ${CLOCK[reference.clock]}, UTC` : ""}`;
  const excluded = bound.length ? <p className="portal-context-note">Production robots only. {names(bound)} {bound.length === 1 ? "is" : "are"} not included: measured values are shown under Connected device.</p> : null;
  if (!summary || !reference) {
    const production = rows.filter(row => row.robot.role === "production" && !row.bound).length;
    return <section className="cfg-section" aria-labelledby="cd-latency-title">
      <div className="portal-section-label"><h2 id="cd-latency-title">Edge vs cloud latency</h2><span>Production robots</span></div>
      <p className="portal-empty cd-empty-note">No production latency series {production ? "is stored for this configuration yet" : "yet: no production robots are attached"}.</p>
    </section>;
  }
  const planner = revision.edgeModels.find(model => model.role === "planner");
  const policy = revision.cloudModels.find(model => model.role === "policy");
  const budget = revision.flagRules.edgeP95BudgetMs;
  const trigger = revision.routing.fallbackTrigger;
  const robots = plural(summary.robots, "production robot");
  const plot = (series: LatencySeries, refValue: number | null) => {
    const ticks = niceTicks(Math.max(...series.p95.filter(finite), ...series.p50.filter(finite), finite(refValue) ? refValue : 0));
    return { ticks, domain: [0, ticks.at(-1) ?? 1] as [number, number], xLabels: latencyTicks(series, now), cadence: cadenceLabel(series.stepS) };
  };
  const edgePlot = edge ? plot(edge, budget) : null;
  const cloudPlot = cloud ? plot(cloud, trigger.cloudRttP95Ms) : null;
  const fallbackHours = cloud ? (cloud.fallbackPct ?? []).filter(value => finite(value) && value > 10).length : 0;
  const empty = (text: ReactNode) => <div className="cd-plot-empty"><p className="portal-empty">{text}</p></div>;
  return <div className="cfg-section"><Panel eyebrow={eyebrow} title="Edge vs cloud latency" titleId="cd-latency-title">
    <PlotPair>
      {edge && edgePlot ? <BandPlot title={`Edge · ${planner?.shortName ?? "planner"}, ms`} series="edge" rows={latencyRows(edge)} domain={edgePlot.domain} ticks={edgePlot.ticks}
        reference={budget} referenceLabel={finite(budget) ? `Edge p95 budget ${fmtNumber(budget, 0)} ms` : undefined} xLabels={edgePlot.xLabels}
        legend={[{ key: "line", series: "edge", label: "p50" }, { key: "band", series: "edge", label: "p50–p95" }, ...(finite(budget) ? [{ key: "ref" as const, label: "Edge p95 budget" }] : [])]}
        note={`${seriesWindowLabel(edge)} · ${edgePlot.cadence} p50 and p95 · ${robots}${finite(budget) ? "" : " · no edge budget defined"}`}
        provenance={summary.provenance} now={now}
        label={`Edge planner latency, production robots, ${edgePlot.cadence} over the last ${window}: p50 ${spanText(edge.p50)} ms, p95 ${spanText(edge.p95)} ms; ${finite(budget) ? `edge p95 budget ${fmtNumber(budget, 0)} ms` : "no edge budget defined"}`} />
        : empty("No edge latency series in the production summary.")}
      {cloud && cloudPlot ? <BandPlot title={`Cloud · ${policy?.shortName ?? "policy"} chunk end to end, ms`} series="cloud" rows={latencyRows(cloud, 10)} domain={cloudPlot.domain} ticks={cloudPlot.ticks}
        reference={trigger.cloudRttP95Ms} referenceLabel={finite(trigger.cloudRttP95Ms) ? `Fallback trigger ${fmtNumber(trigger.cloudRttP95Ms, 0)} ms` : undefined} xLabels={cloudPlot.xLabels} eventsAt="top"
        legend={[{ key: "line", series: "cloud", label: "p50" }, { key: "band", series: "cloud", label: "p50–p95" },
          ...(finite(trigger.cloudRttP95Ms) ? [{ key: "ref" as const, label: "Fallback trigger" }] : []),
          ...(cloud.fallbackPct ? [{ key: "square" as const, series: "fallback" as const, label: "Hours with over 10 % of chunks on fallback" }] : [])]}
        note={`Same window · observation to chunk received${finite(trigger.cloudRttP95Ms) ? ` · fallback when cloud RTT p95 > ${fmtNumber(trigger.cloudRttP95Ms, 0)} ms${finite(trigger.windowS) ? ` over ${fmtNumber(trigger.windowS)} s` : ""}` : ""}${finite(summary.fallbackPct) ? ` · ${fmtPct(summary.fallbackPct)} of chunks on fallback in ${window}` : ""}`}
        provenance={summary.provenance} now={now}
        label={`Cloud policy latency, observation to chunk received, ${cloudPlot.cadence} over the last ${window}: p50 ${spanText(cloud.p50)} ms, p95 ${spanText(cloud.p95)} ms${cloud.fallbackPct ? `; more than 10 % of chunks on fallback in ${fmtCount(fallbackHours)} of ${fmtCount(cloud.p50.length)} hours` : ""}`} />
        : empty(revision.routing.mode === "edge-only" ? "No cloud model in this configuration." : "No cloud latency series in the production summary.")}
    </PlotPair>
    {excluded}
  </Panel></div>;
}
