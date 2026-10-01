"use client";

import Link from "next/link";
import { useState } from "react";
import type { ReactNode } from "react";
import { Badge, NotReported, ProvenanceBadge } from "@/components/configurations/Badges";
import { Sparkline } from "@/components/configurations/Charts";
import { DataTable } from "@/components/configurations/DataTable";
import { Fact, FactPanel, Section, type FactItem } from "@/components/configurations/Facts";
import { Icon } from "@/components/configurations/Icons";
import { KpiGrid, KpiTile } from "@/components/configurations/Kpi";
import { fmtCount, fmtDate, fmtDateTime, fmtMs, fmtNumber, fmtPct, fmtRange, fmtRelative, fmtTime, fmtUnit, NOT_REPORTED } from "@/lib/configurations/format";
import type { LiveBinding, LiveDeviceData } from "@/lib/configurations/live";
import { clockText, edgeSpanStats, MODEL_ROLE_LABEL, MODEL_STATE_LABEL, ROBOT_KIND_LABEL, timeWindowLabel } from "@/lib/configurations/robot";
import { routes } from "@/lib/configurations/routes";
import { getSuite } from "@/lib/configurations/selectors";
import type { RobotReadings } from "@/lib/configurations/selectors";
import { CONFIGURED_DEVICE } from "@/lib/configurations/types";
import type { ClockLabel, ConfigRevision, Configuration, ConvoyWorkspace, FlagRule, Percentiles, Provenance, Robot, TelemetryMetric } from "@/lib/configurations/types";

const NONE: Provenance = { kind: "not-reported" };

/** Age of a time ("9s ago"); before the first clock tick it shows the time itself, never a false "Not reported". */
export function ago(at: string | null | undefined, now: number | null, clock?: ClockLabel): string {
  if (!at || !Number.isFinite(Date.parse(at))) return NOT_REPORTED;
  return now === null ? fmtTime(at, clock) : fmtRelative(at, now);
}

/** Fact detail with its provenance badge first (`rb-prov`). */
function Evidence({ provenance, now, children }: { provenance: Provenance; now: number | null; children?: ReactNode }) {
  return <><ProvenanceBadge provenance={provenance} now={now} />{children}</>;
}
function Strip({ facts, className = "" }: { facts: readonly FactItem[]; className?: string }) {
  return <dl className={`portal-health-strip rb-prov${className ? ` ${className}` : ""}`}>{facts.map((fact, i) => <Fact key={i} {...fact} />)}</dl>;
}

/* ---------- telemetry tiles: measured (live binding) or stored sample values, never both ---------- */

/** `noun` is the label inside a sentence (acronyms keep their case). */
const METRICS: ReadonlyArray<{ key: TelemetryMetric; label: string; noun: string; unit: string; digits: number; rule: FlagRule | null }> = [
  { key: "cpuPct", label: "CPU utilization", noun: "CPU utilization", unit: "%", digits: 0, rule: null },
  { key: "memAvailableMiB", label: "Memory available", noun: "memory available", unit: "MiB", digits: 0, rule: "memory" },
  { key: "socTempC", label: "Jetson SoC temperature", noun: "Jetson SoC temperature", unit: "°C", digits: 1, rule: "soc-temp" },
  { key: "boardPowerW", label: "Board input power", noun: "board input power", unit: "W", digits: 1, rule: "board-power" },
];

/** Tiles while a bound robot has no device report: the value is "Not reported" (its own provenance), with the reason. */
function NoReportTiles({ reason }: { reason: string }) {
  return <KpiGrid>{METRICS.map(metric => <section className="portal-metric-card cfg-kpi" key={metric.key}>
    <h3>{metric.label}</h3>
    <div className="portal-metric-value"><NotReported /></div>
    <p className="cfg-kpi__sub">{reason}</p>
  </section>)}</KpiGrid>;
}

function TelemetryTiles({ robot, readings, now, maxGapMs, clock }: { robot: Robot; readings: RobotReadings; now: number | null; maxGapMs?: number; clock?: ClockLabel }) {
  const measured = readings.provenance.kind === "measured";
  const total = readings.latest?.memTotalMiB ?? null;
  return <KpiGrid>
    {METRICS.map(metric => {
      const points = readings.recent[metric.key] ?? [];
      const reported = points.filter(point => point.value !== null);
      const value = readings.latest?.[metric.key] ?? null;
      const flag = metric.rule ? readings.flags.find(item => item.rule === metric.rule) : undefined;
      const range = fmtRange(points.map(point => point.value), metric.unit, metric.digits);
      const window = timeWindowLabel(points.map(point => point.at), clock);
      const label = `${robot.name} ${metric.noun}: ${reported.length} ${measured ? "measured" : "sample"} values${window ? `, ${window}` : ""}, ${range}, latest ${fmtUnit(value, metric.unit, metric.digits)}. Gaps are not filled.`;
      return <KpiTile key={metric.key} label={metric.label} value={value} unit={metric.unit} digits={metric.digits} provenance={readings.provenance} now={now}
        sub={flag ? <span className="cfg-flagnote"><Icon name="flag" small />{flag.label}</span> : metric.key === "memAvailableMiB" && total !== null ? `of ${fmtNumber(total, 0)} MiB` : undefined}>
        <Sparkline points={points} label={label} unit={metric.unit} digits={metric.digits} series={flag ? "flag" : null} maxGapMs={maxGapMs}
          caption={<>{range} <span>{reported.length} {measured ? "measured samples" : "samples"}</span></>} empty={`No ${metric.noun} values in this window.`} />
      </KpiTile>;
    })}
  </KpiGrid>;
}

/** Exact measured values behind the sparklines, newest first. */
function TelemetrySamples({ readings, clock }: { readings: RobotReadings; clock?: ClockLabel }) {
  const rows = new Map<string, Partial<Record<TelemetryMetric, number | null>>>();
  for (const metric of ["cpuPct", "gpuPct", "memAvailableMiB", "socTempC", "boardPowerW"] as const) {
    for (const point of readings.recent[metric] ?? []) rows.set(point.at, { ...rows.get(point.at), [metric]: point.value });
  }
  const samples = [...rows.entries()].map(([at, values]) => ({ at, ...values })).toSorted((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const cell = (value: number | null | undefined, digits = 1) => value === null || value === undefined ? <NotReported /> : fmtNumber(value, digits);
  return <details className="portal-details">
    <summary>Inspect telemetry samples <span>{fmtCount(samples.length)} received</span></summary>
    <DataTable label="Telemetry samples" caption="Exact telemetry samples, newest first" rows={samples} rowKey={row => row.at} className="rb-table" columns={[
      { key: "at", header: `Sample time (${clock?.zone ?? "UTC"})`, cell: row => fmtDateTime(row.at, clock) },
      { key: "cpu", header: "CPU (%)", numeric: true, cell: row => cell(row.cpuPct, 0) },
      { key: "gpu", header: "GPU (%)", numeric: true, cell: row => cell(row.gpuPct, 0) },
      { key: "mem", header: "Memory available (MiB)", numeric: true, cell: row => cell(row.memAvailableMiB, 0) },
      { key: "temp", header: "Jetson SoC (°C)", numeric: true, cell: row => cell(row.socTempC) },
      { key: "power", header: "Board input (W)", numeric: true, cell: row => cell(row.boardPowerW) },
    ]} empty="No telemetry samples received." />
  </details>;
}

/* ---------- device-bound robot: the connected device, measured ---------- */

function runtimeText(data: LiveDeviceData): string | null {
  const release = data.release;
  if (!release) return null;
  return [[release.runtime, release.backend].filter(Boolean).join(" · "), release.contextWindow !== null ? `${fmtNumber(release.contextWindow, 0)} ctx` : null].filter(Boolean).join(" · ") || null;
}

/**
 * Hero, health strip, telemetry tiles and edge inference latency for a robot bound
 * to a live device. Every value here is measured; while no report has arrived the
 * page says so instead of showing zeros.
 */
export function DeviceOverview({ robot, configuration, revision, live, readings, now, onRefresh }: {
  robot: Robot; configuration: Configuration; revision: ConfigRevision | null; live: LiveBinding; readings: RobotReadings; now: number | null; onRefresh: () => Promise<void>;
}) {
  const [checking, setChecking] = useState(false);
  const data = live.data;
  const stale = live.status === "stale";
  const hardwareName = data?.hardware?.model ?? revision?.edgeHardware.name ?? "Edge device";
  function check() {
    setChecking(true);
    void onRefresh().finally(() => setChecking(false));
  }
  const checkButton = <button className="btn btn-secondary cfg-btn" type="button" disabled={checking} onClick={check}>{checking ? "Checking…" : "Check again"}<span aria-hidden="true">↻</span></button>;
  if (!data) {
    const waiting = live.status === "loading";
    return <>
      <section className="portal-device-board rb-board" aria-labelledby="rb-hero-title">
        <div className="portal-device-model">
          <p className="portal-eyebrow">Edge device · {hardwareName}</p>
          <h2 id="rb-hero-title">{waiting ? "Waiting for the first device report" : "No device report is available right now"}</h2>
          <p className="portal-model-repo">{waiting ? "Reading device state and measurements…" : live.error ?? "The device did not answer."}</p>
          <div className="portal-inline-facts"><NotReported /><span>{robot.deviceId === CONFIGURED_DEVICE ? "Bound to the workspace’s connected device" : `Bound to device ${robot.deviceId}`}</span></div>
        </div>
        {!waiting && checkButton}
      </section>
      <Section title="Device telemetry" note="Measured values appear when the device reports" id="rb-telemetry">
        <NoReportTiles reason={waiting ? "Waiting for the first device report" : "No device report available"} />
      </Section>
    </>;
  }
  const latest = data.latest;
  const sampleStale = !!(latest?.at && now !== null && data.staleAfterS !== null && now - Date.parse(latest.at) > data.staleAfterS * 1000);
  // Same liveness rule as the device view: "Online" only for a fresh live report with no terminal identity state.
  const identity = data.identityState ? data.identityState.charAt(0).toUpperCase() + data.identityState.slice(1) : null;
  const liveness = identity ?? (stale ? "Refresh needed" : data.online ? "Online" : "No recent live contact");
  const contact: Provenance = { kind: "measured", at: data.liveAt ?? undefined, source: data.name };
  const stats = edgeSpanStats(data.inference);
  const spans: Provenance = stats.latency.n ? { kind: "measured", at: stats.to ?? data.fetchedAt, n: stats.latency.n, source: `${data.name} gateway` } : NONE;
  const mode = revision?.edgeHardware.powerModes.find(item => item.id === revision.edgeHardware.powerModeId);
  const window = timeWindowLabel(readings.recent.socTempC?.map(point => point.at) ?? []);
  const recorded = robot.latency && robot.latency.provenance.kind !== "not-reported" ? robot.latency : null;
  const runtime = runtimeText(data);
  return <>
    <section className="portal-device-board rb-board" aria-labelledby="rb-hero-title">
      <div className="portal-device-drawing" aria-hidden="true"><svg viewBox="0 0 180 138"><rect x="30" y="25" width="120" height="88" rx="2" /><rect x="62" y="45" width="56" height="48" rx="2" /><path d="M75 55h30v28H75zM18 43h12m-12 13h12m-12 13h12m-12 13h12m-12 13h12m120-52h12m-12 13h12m-12 13h12m-12 13h12m-12 13h12M49 13v12m15-12v12m15-12v12m15-12v12m15-12v12m15-12v12M49 113v12m15-12v12m15-12v12m15-12v12m15-12v12m15-12v12" /><circle cx="42" cy="37" r="3" /><circle cx="138" cy="101" r="3" /></svg><span>Edge device</span></div>
      <div className="portal-device-model">
        <p className="portal-eyebrow">Running release · {hardwareName}</p>
        <h2 id="rb-hero-title">{data.release?.name ?? "No release reported"}</h2>
        <p className="portal-model-repo">{[data.release?.modelRepo ?? "Model repository not reported", runtime].filter(Boolean).join(" · ")}</p>
        <div className="portal-inline-facts">
          <Badge tone={liveness === "Online" ? "success" : "warning"}>{liveness}</Badge>
          {data.online && !stale && data.chat && <Badge tone={data.chat.eligible ? "success" : "warning"}>{data.chat.eligible ? "Ready for inference" : "Inference unavailable"}</Badge>}
          <span>{data.runtimeState ?? "Runtime state not reported"}</span>
          <ProvenanceBadge provenance={data.provenance} now={now} />
        </div>
      </div>
      {data.source === "portal-snapshot" ? <Link className="btn btn-secondary cfg-btn" href={routes.chat()}>Open chat <span aria-hidden="true">↗</span></Link> : stale ? checkButton : null}
    </section>
    {data.chat && !data.chat.eligible && <p className="portal-context-note">{data.chat.reason ?? "The device is not currently ready to accept inference requests."}</p>}
    {data.source === "platform-device" && <p className="portal-context-note">Chat and inference traces are available for the workspace’s configured device only; this robot reads its device’s latest telemetry.</p>}

    <Strip facts={[
      { label: "Device contact", value: ago(data.liveAt, now), detail: <Evidence provenance={data.liveAt ? contact : NONE} now={null}>{fmtDateTime(data.liveAt)}</Evidence> },
      { label: "Observed health", value: data.observedHealth ?? NOT_REPORTED, detail: <Evidence provenance={data.observedAt ? { kind: "measured", at: data.observedAt, source: data.name } : NONE} now={null}>Observed {fmtDateTime(data.observedAt)}</Evidence> },
      { label: "Latest telemetry", value: ago(latest?.at, now), detail: <Evidence provenance={readings.provenance} now={null}>{sampleStale ? `Sample is stale · ${fmtDateTime(latest?.at)}` : fmtDateTime(latest?.at)}</Evidence> },
    ]} />

    <Section title="Device telemetry" note={sampleStale || stale ? "Last known measurements · stale" : "Latest reported measurements · one sample per heartbeat"} id="rb-telemetry">
      <TelemetryTiles robot={robot} readings={readings} now={now} maxGapMs={(data.staleAfterS ?? 30) * 1000} />
      <p className="portal-context-note">{window ? `${window}. ` : "No telemetry samples received. "}Dots represent received samples. Missing values and gaps longer than {data.staleAfterS ?? 30} seconds are left open.</p>
      <TelemetrySamples readings={readings} />
      <Strip className="rb-strip" facts={[
        { label: "GPU utilization", value: fmtUnit(latest?.gpuPct, "%", 0), detail: <Evidence provenance={latest?.gpuPct != null ? readings.provenance : NONE} now={now}>Latest sample</Evidence> },
        { label: "Runtime state", value: data.runtimeState ?? NOT_REPORTED, detail: <Evidence provenance={data.runtimeState ? readings.provenance : NONE} now={now}>{runtime ?? "Runtime not reported"}</Evidence> },
        { label: "Power mode", value: NOT_REPORTED, detail: `Device telemetry does not include the power mode${mode ? `; ${configuration.name} ${revision?.rev} declares ${mode.label}` : ""}.` },
      ]} />
    </Section>

    <Section title="Edge inference latency" note={`On-device gateway · ${fmtCount(stats.received)} received requests · browser and relay time excluded`} id="rb-latency">
      <KpiGrid>
        <KpiTile label="Device latency p50" series="edge" value={stats.latency.p50} unit="ms" digits={0} provenance={spans} now={now}
          sub={stats.latency.n ? `p95 ${fmtMs(stats.latency.p95)} · n = ${fmtCount(stats.latency.n)} requests` : "No requests received yet"} />
        <KpiTile label="Time to first token p50" series="edge" value={stats.ttft.p50} unit="ms" digits={0} provenance={stats.ttft.n ? { ...spans, n: stats.ttft.n } : NONE} now={now}
          sub={stats.ttft.n ? `p95 ${fmtMs(stats.ttft.p95)} · n = ${fmtCount(stats.ttft.n)} · gateway-internal` : "Not reported by the gateway"} />
        <KpiTile label="Reported throughput p50" series="edge" value={stats.throughput.p50} unit="tokens/s" digits={1} provenance={stats.throughput.n ? { ...spans, n: stats.throughput.n } : NONE} now={now}
          sub={stats.throughput.n ? `${fmtNumber(stats.outputTokens.p50, 0)} output tokens per request (median) · n = ${fmtCount(stats.throughput.n)}` : "Not reported by the gateway"} />
        {recorded ? <KpiTile label={`Planner latency p50 · ${recorded.window}`} series="edge" value={recorded.edgePlannerMs.p50} unit="ms" digits={0} provenance={recorded.provenance} now={now}
          sub={[`p95 ${fmtMs(recorded.edgePlannerMs.p95)}`, recorded.ttftMs ? `TTFT ${fmtNumber(recorded.ttftMs.p50, 0)} / ${fmtMs(recorded.ttftMs.p95)}` : null, recorded.edgePlannerMs.n ? `n = ${fmtCount(recorded.edgePlannerMs.n)}` : "n not reported"].filter(Boolean).join(" · ")} />
          : <KpiTile label="Queue time p50" series="edge" value={stats.queue.p50} unit="ms" digits={0} provenance={stats.queue.n ? { ...spans, n: stats.queue.n } : NONE} now={now} sub={stats.queue.n ? `p95 ${fmtMs(stats.queue.p95)} · n = ${fmtCount(stats.queue.n)}` : "Not reported by the gateway"} />}
      </KpiGrid>
      <p className="portal-context-note">Device latency is the gateway’s model time for each request, measured on the device; browser and relay time are excluded and queue time is separate. The sample can include any request to this device’s gateway, such as chat tests.{recorded ? " The recorded figure is stored evidence and is not combined with the measured sample." : ""}</p>
    </Section>
  </>;
}

/* ---------- robots without a live binding: stored sample or recorded values ---------- */

function latencySub(stat: Percentiles | null | undefined, extra?: string | null): string {
  if (!stat) return "Not reported";
  return [`p95 ${fmtMs(stat.p95)}`, stat.n ? `n = ${fmtCount(stat.n)}` : "n not reported", extra].filter(Boolean).join(" · ");
}

/** Health strip, telemetry tiles and edge / cloud figures from the workspace document (Sample or Recorded, each badged). */
export function RobotOverview({ robot, configuration, revision, readings, now }: { robot: Robot; configuration: Configuration; revision: ConfigRevision | null; readings: RobotReadings; now: number | null }) {
  const latest = readings.latest;
  const telemetry = !!robot.telemetry;
  const prov = readings.provenance;
  const latency = robot.latency ?? null;
  const window = timeWindowLabel(readings.recent.socTempC?.map(point => point.at) ?? [], robot.clock);
  const revState = robot.rev === configuration.productionRev ? "in production" : robot.rev === configuration.candidateRev ? "testing" : robot.rev ? "earlier revision" : "no revision";
  return <>
    <Strip facts={[
      { label: "Configuration", value: robot.rev ?? NOT_REPORTED, detail: `${configuration.name} · ${revState}` },
      { label: "Site", value: robot.site, detail: [revision?.robot.name, ROBOT_KIND_LABEL[robot.kind]].filter(Boolean).join(" · ") },
      // Without telemetry the contact time is a stored fact of the robot, not a reading: no provenance is claimed for it.
      { label: "Device contact", value: ago(readings.lastSeenAt, now, robot.clock), detail: !readings.lastSeenAt ? undefined
        : telemetry ? <Evidence provenance={prov} now={now}>{fmtDateTime(readings.lastSeenAt, robot.clock)}</Evidence> : `${fmtDateTime(readings.lastSeenAt, robot.clock)} · stored with the robot` },
      ...(telemetry ? [
        { label: "Battery", value: fmtUnit(latest?.batteryPct, "%", 0), detail: <Evidence provenance={latest?.batteryPct != null ? prov : NONE} now={now}>{latest?.at ? `Latest sample ${fmtTime(latest.at, robot.clock)}` : null}</Evidence> },
        { label: "GPU utilization", value: fmtUnit(latest?.gpuPct, "%", 0), detail: <Evidence provenance={latest?.gpuPct != null ? prov : NONE} now={now}>{revision ? `Latest sample · ${revision.edgeHardware.name}` : "Latest sample"}</Evidence> },
        { label: "Device clock", value: clockText(robot.clock), detail: "Trace and log times on this page" },
      ] : []),
    ]} />
    <Section title="Device telemetry" note={telemetry ? "Latest reported values · one sample about every 15 seconds" : "No device telemetry"} id="rb-telemetry">
      {telemetry ? <>
        <TelemetryTiles robot={robot} readings={readings} now={now} clock={robot.clock} />
        <p className="portal-context-note">{window ? `${window}, device clock. ` : ""}Dots represent stored samples. {prov.kind === "sample" ? "Sample values prepared for discussion; they are not measurements." : prov.kind === "recorded" ? "Recorded values from stored evidence." : ""}</p>
      </> : <p className="portal-empty">{robot.kind === "simulator" ? `${robot.name} is a simulated robot; it reports no device telemetry.` : robot.healthReason ?? `${robot.name} has not reported device telemetry.`}</p>}
    </Section>
    {(latency || robot.interventions) && <Section title="Edge and cloud" note={latency ? `Window: ${latency.window}` : undefined} id="rb-edge-cloud">
      <KpiGrid>
        <KpiTile label="Edge planner p50" series="edge" value={latency?.edgePlannerMs.p50 ?? null} unit="ms" digits={0} sub={latencySub(latency?.edgePlannerMs)} provenance={latency?.provenance ?? NONE} now={now} />
        <KpiTile label="Cloud chunk p50" series="cloud" value={latency?.cloudChunkMs?.p50 ?? null} unit="ms" digits={0} sub={latency?.cloudChunkMs ? latencySub(latency.cloudChunkMs, "end to end") : "No cloud path reported"} provenance={latency?.cloudChunkMs ? latency.provenance : NONE} now={now} />
        <KpiTile label="Fallback" series="fallback" value={latency?.fallbackPct ?? null} unit="% of chunks" digits={1} sub={latency?.cloudTimeoutPct != null ? `Cloud timeouts ${fmtPct(latency.cloudTimeoutPct)}` : undefined} provenance={latency?.fallbackPct != null ? latency.provenance : NONE} now={now} />
        <KpiTile label="Interventions" value={robot.interventions?.perHour ?? null} unit="per robot-hour" digits={1} provenance={robot.interventions?.perHour != null ? robot.interventions.provenance : NONE} now={now}
          sub={robot.interventions?.robotHours != null ? `${fmtNumber(robot.interventions.robotHours, 1)} robot-hours` : undefined} />
      </KpiGrid>
    </Section>}
  </>;
}

/* ---------- metadata ---------- */

/** Three fact panels: robot and configuration, edge device (reported or declared), models of the revision. */
export function RobotFacts({ workspace, robot, configuration, revision, live, now }: { workspace: ConvoyWorkspace; robot: Robot; configuration: Configuration; revision: ConfigRevision | null; live: LiveBinding; now: number | null }) {
  const suite = getSuite(workspace, configuration.suiteId);
  const spec = revision?.robot ?? null;
  const simulated = robot.kind !== "robot";
  const data = robot.deviceId ? live.data : null;
  const hardware = revision?.edgeHardware ?? null;
  const mode = hardware?.powerModes.find(item => item.id === hardware.powerModeId);
  const revNote = robot.rev === configuration.productionRev ? "In production" : robot.rev === configuration.candidateRev ? `Testing${configuration.productionRev ? ` · production runs ${configuration.productionRev}` : ""}` : "Earlier revision";
  const robotFacts: FactItem[] = [
    simulated && spec?.simTwin
      ? { label: "Robot", value: spec.simTwin.name, detail: `${spec.simTwin.engine} · simulated${robot.kind === "bench" ? "; no physical robot on this bench" : ""}` }
      : { label: "Robot", value: spec?.name ?? NOT_REPORTED, detail: spec?.summary },
    { label: robot.kind === "bench" ? "Bench" : "Site", value: robot.site, detail: robot.site.toLowerCase().includes(ROBOT_KIND_LABEL[robot.kind].toLowerCase()) ? undefined : ROBOT_KIND_LABEL[robot.kind] },
    { label: "Configuration", value: `${configuration.name} ${robot.rev ?? ""}`.trim(), detail: revNote },
    { label: "Eval suite", value: suite ? `${suite.name} ${suite.version}` : NOT_REPORTED, detail: suite ? `${fmtCount(suite.episodesPerRun)} episodes per run` : undefined },
    { label: "Registered", value: fmtDate(robot.registeredAt) },
  ];
  const deviceFacts: FactItem[] = robot.deviceId ? (data ? [
    { label: "Hardware", value: data.hardware?.model ?? hardware?.name ?? NOT_REPORTED, detail: data.hardware ? [data.hardware.l4tRelease ? `L4T ${data.hardware.l4tRelease}` : null, data.hardware.cudaVersion ? `CUDA ${data.hardware.cudaVersion}` : null, data.hardware.computeCapability ? `SM ${data.hardware.computeCapability}` : null].filter(Boolean).join(" · ") || undefined : "Declared by the configuration; the device has not reported its inventory" },
    { label: "Device ID", value: data.deviceId },
    { label: "Agent version", value: data.agentVersion ?? NOT_REPORTED },
    { label: "Release ID", value: data.releaseId ?? NOT_REPORTED },
    { label: "Total memory", value: fmtUnit(data.latest?.memTotalMiB ?? data.hardware?.memTotalMiB, "MiB", 0) },
  ] : [
    { label: "Binding", value: robot.deviceId === CONFIGURED_DEVICE ? "Workspace’s connected device" : robot.deviceId, detail: live.status === "loading" ? "Waiting for the first report" : live.error ?? "No device report is available right now" },
    { label: "Hardware", value: hardware?.name ?? NOT_REPORTED, detail: "Declared by the configuration" },
  ]) : [
    { label: "Hardware", value: hardware?.name ?? NOT_REPORTED, detail: hardware?.compute },
    { label: "Software", value: hardware?.software ?? NOT_REPORTED },
    { label: "Memory", value: hardware ? `${fmtNumber(hardware.memoryGiB, 1)} GiB` : NOT_REPORTED, detail: hardware?.memoryNote },
    { label: "Power mode", value: mode?.label ?? NOT_REPORTED, detail: mode?.note },
    { label: "Agent version", value: robot.agentVersion ?? NOT_REPORTED },
  ];
  const models: FactItem[] = revision ? [
    ...revision.edgeModels.map(model => ({ label: `${MODEL_ROLE_LABEL[model.role]} · edge`, value: model.name, detail: <>{model.evidence && <ProvenanceBadge provenance={model.evidence} now={now} />}{[model.runtime, model.state === "active" ? null : MODEL_STATE_LABEL[model.state]].filter(Boolean).join(" · ")}</> })),
    ...revision.cloudModels.map(model => ({ label: `${MODEL_ROLE_LABEL[model.role]} · cloud`, value: model.name, detail: <>{model.evidence && <ProvenanceBadge provenance={model.evidence} now={now} />}{[model.serving, model.state === "active" ? null : MODEL_STATE_LABEL[model.state]].filter(Boolean).join(" · ")}</> })),
  ] : [{ label: "Models", value: NOT_REPORTED, detail: "The robot is not on a revision of this configuration" }];
  return <Section title="Robot, device and models" note={`${configuration.name} ${robot.rev ?? ""}${data?.agentVersion ? ` · device state reported by agent ${data.agentVersion}` : ""}`} id="rb-facts">
    <div className="rb-facts3 rb-prov">
      <FactPanel eyebrow="Embodiment" title="Robot and configuration" titleId="rb-facts-robot" facts={robotFacts} action={spec ? <ProvenanceBadge provenance={spec.provenance} now={now} /> : undefined} />
      <FactPanel eyebrow={robot.deviceId ? "Reported state" : "Declared"} title="Edge device" titleId="rb-facts-device" facts={deviceFacts}
        action={data ? <ProvenanceBadge provenance={{ kind: "measured", at: data.fetchedAt, source: data.name }} now={now} /> : undefined} />
      <FactPanel eyebrow={`Configuration ${revision?.rev ?? ""}`.trim()} title="Models" titleId="rb-facts-models" facts={models} />
    </div>
  </Section>;
}
