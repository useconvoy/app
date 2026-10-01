"use client";

import Link from "next/link";
import { useState } from "react";
import { Badge, NotReported, OutcomeBadge, PathChip, ProvenanceBadge, RunStatus } from "@/components/configurations/Badges";
import { LegendKey } from "@/components/configurations/Charts";
import { DataTable, type Column } from "@/components/configurations/DataTable";
import { Panel } from "@/components/configurations/Facts";
import { LogPanel } from "@/components/configurations/LogPanel";
import { EmptyState } from "@/components/configurations/States";
import { FilterChips } from "@/components/configurations/Toolbar";
import { fmtCount, fmtFixed, fmtMs, fmtNumber, fmtSeconds, fmtTime, runLabel } from "@/lib/configurations/format";
import type { LiveDeviceData, LiveInference } from "@/lib/configurations/live";
import {
  edgeSpanStats, latencyBarChart, policyLabel, runRowFacts, timeWindowLabel, TRACE_EMPTY, TRACE_FILTER_LABEL, TRACE_FILTERS, traceCounts, type RunCell,
} from "@/lib/configurations/robot";
import { routes } from "@/lib/configurations/routes";
import { logsFor, runsFor, successShare, tracesFor, type TraceFilter } from "@/lib/configurations/selectors";
import type { ActionTrace, ConvoyWorkspace, EvalRun, Provenance, Robot } from "@/lib/configurations/types";

/** Distinct provenance badges of a set of items (never merged into one claim). */
function ProvenanceSet({ items, now }: { items: ReadonlyArray<{ provenance: Provenance }>; now: number | null }) {
  const seen = new Map<string, Provenance>();
  for (const item of items) seen.set(`${item.provenance.kind}:${item.provenance.at ?? ""}`, item.provenance);
  return <>{[...seen.values()].map(provenance => <ProvenanceBadge key={`${provenance.kind}:${provenance.at ?? ""}`} provenance={provenance} now={now} />)}</>;
}
function Value({ cell }: { cell: RunCell }) { return cell.missing ? <NotReported>{cell.value}</NotReported> : <>{cell.value}</>; }

/* ---------- Evals & sims ---------- */

/** Evaluation runs on the robot (running and queued first, then newest), each row links to its run page. */
export function RunsPanel({ workspace, robot, configurationId, now }: { workspace: ConvoyWorkspace; robot: Robot; configurationId: string; now: number | null }) {
  const runs = runsFor(workspace, { robotId: robot.id });
  const href = (run: EvalRun) => routes.run(configurationId, robot.id, run.id);
  const facts = new Map(runs.map(run => [run.id, runRowFacts(workspace, run)]));
  const row = (run: EvalRun) => facts.get(run.id)!;
  const evidence = (run: EvalRun): Provenance => run.recordedEvaluationId && run.provenance.kind !== "recorded" ? { kind: "recorded", at: run.finishedAt ?? run.startedAt ?? undefined, source: `Evaluation ${run.recordedEvaluationId}` } : run.provenance;
  const columns: Array<Column<EvalRun>> = [
    { key: "run", header: "Run", cell: run => runLabel(run), detail: run => run.title },
    { key: "subject", header: "Under test", wrap: true, cell: run => row(run).subject, detail: run => row(run).subjectDetail },
    { key: "started", header: "Started (UTC)", cell: run => row(run).started, sort: run => run.startedAt ? Date.parse(run.startedAt) : null, firstDir: "desc" },
    { key: "success", header: "Success", numeric: true, cell: run => <Value cell={row(run).success} />, detail: run => row(run).success.detail, sort: run => successShare(run), firstDir: "desc" },
    { key: "safety", header: "Safety / 100", numeric: true, cell: run => <Value cell={row(run).safety} />, detail: run => row(run).safety.detail, sort: run => run.safety?.per100 ?? null, firstDir: "desc" },
    { key: "median", header: "Median time", numeric: true, cell: run => <Value cell={row(run).median} />, detail: run => row(run).median.detail },
    { key: "result", header: "Result", cell: run => <RunStatus run={run} />, detail: run => row(run).note },
    { key: "evidence", header: "Evidence", cell: run => <ProvenanceBadge provenance={evidence(run)} now={now} />, detail: run => row(run).evaluationId ? `Evaluation ${row(run).evaluationId}` : null },
    { key: "open", header: "Open", srOnly: true, cell: run => <Link className="portal-table-link" href={href(run)}>Open run<span className="cfg-sr"> {run.number}</span></Link> },
  ];
  return <Panel eyebrow={`${robot.name} · runs and recorded evidence`} title="Evaluation runs" titleId="rb-runs-title" action={<span className="cfg-ver">{fmtCount(runs.length)} {runs.length === 1 ? "run" : "runs"}</span>}>
    {runs.length ? <>
      <p className="rb-intro">Running and queued runs first, then newest. Success shows n and the 95 % confidence interval when scored; the clock of each median time is noted under it. Queued runs wait for an evaluation runner.</p>
      <DataTable label="Evaluation runs" className="rb-table rb-sr-caption" rows={runs} rowKey={run => run.id} rowHref={href} columns={columns} caption={`Evaluation runs on ${robot.name}`} />
    </> : <EmptyState icon="info" title={`No evaluation runs on ${robot.name} yet.`} text="Run evaluation queues a suite run on this robot. Results appear here when a runner reports them." />}
  </Panel>;
}

/* ---------- Traces: the device's own gateway spans (measured) ---------- */

const SHOWN = 6;
/** Latest received on-device inference spans: per-request latency bars and the exact records. */
export function InferencePanel({ data, robot, now }: { data: LiveDeviceData | null; robot: Robot; now: number | null }) {
  const [all, setAll] = useState(false);
  if (!data) return <Panel eyebrow="On-device gateway" title="Edge inference traces" titleId="rb-inference-title"><p className="portal-empty">No device report is available, so no inference traces can be shown.</p></Panel>;
  if (data.source !== "portal-snapshot") return <Panel eyebrow="On-device gateway" title="Edge inference traces" titleId="rb-inference-title"><p className="portal-empty">Inference traces are available for the workspace’s configured device only.</p></Panel>;
  const spans = data.inference;
  const stats = edgeSpanStats(spans);
  const chart = latencyBarChart(spans);
  const provenance: Provenance = spans.length ? { kind: "measured", at: stats.to ?? data.fetchedAt, n: spans.length, source: `${data.name} gateway` } : { kind: "not-reported" };
  const window = timeWindowLabel(spans.map(span => span.at));
  const shown = all ? spans : spans.slice(0, SHOWN);
  const columns: Array<Column<LiveInference>> = [
    { key: "at", header: "Started (UTC)", cell: span => span.at ? fmtTime(span.at) : <NotReported /> },
    { key: "trace", header: "Trace", cell: span => span.traceId },
    { key: "status", header: "Status", cell: span => span.status ? <Badge tone={span.status === "ok" ? "success" : "warning"}>{span.status}</Badge> : <NotReported /> },
    { key: "latency", header: "Device latency", numeric: true, cell: span => span.latencyMs === null ? <NotReported /> : `${fmtFixed(span.latencyMs, 1)} ms` },
    { key: "ttft", header: "Time to first token", numeric: true, cell: span => span.ttftMs === null ? <NotReported /> : `${fmtFixed(span.ttftMs, 1)} ms` },
    { key: "tokens", header: "Input / output tokens", numeric: true, cell: span => `${fmtNumber(span.tokensIn, 0)} / ${fmtNumber(span.tokensOut, 0)}` },
    { key: "throughput", header: "Reported throughput", numeric: true, cell: span => span.tokensPerS === null ? <NotReported /> : `${fmtFixed(span.tokensPerS, 1)} tokens/s` },
  ];
  return <Panel eyebrow="Latest received inference traces · on-device gateway" title="Edge inference traces" titleId="rb-inference-title" action={<ProvenanceBadge provenance={provenance} now={now} />}>
    {spans.length ? <>
      <p className="rb-intro">{fmtCount(spans.length)} received traces from {robot.name}{window ? `, ${window}` : ""}: the same bounded sample as the latency figures above. Inference on the device; these are not robot actions.</p>
      {chart.count > 0 && <figure className="cfg-plot cfg-plot--sm cfg-series--edge rb-chart">
        <figcaption className="cfg-plot__head">
          <span className="cfg-plot__title"><LegendKey series="edge" />Device latency per request · ms</span>
          <span className="cfg-legend"><span><LegendKey kind="ref" />p95 of this sample</span><span>Median {fmtMs(stats.latency.p50)} · n = {fmtCount(stats.latency.n)}</span></span>
        </figcaption>
        <div className="cfg-plot__body">
          <div className="cfg-plot__y" aria-hidden="true">{chart.ticks.map(tick => <span key={tick.value} style={{ top: tick.top }}>{tick.label}</span>)}</div>
          <div className="cfg-plot__frame">
            <svg className="cfg-plot__svg" viewBox="0 0 100 100" preserveAspectRatio="none" role="img"
              aria-label={`Measured device latency for ${chart.count} requests, oldest to newest, ${fmtMs(chart.low, 1)} to ${fmtMs(chart.high, 1)}; p95 ${fmtMs(chart.p95, 1)}. Exact values are in the inference records table.`}>
              <path className="cfg-plot__grid" d={chart.grid} />
              <path className="portal-latency-bar" d={chart.bars} />
              {chart.ref && <path className="cfg-plot__ref" d={chart.ref} />}
            </svg>
            {chart.refTop && <span className="cfg-plot__ref-label" style={{ top: chart.refTop }}>p95 {fmtMs(chart.p95)}</span>}
          </div>
        </div>
        <div className="cfg-plot__x" aria-hidden="true"><span style={{ left: "0%" }}>Oldest</span><span style={{ left: "100%" }}>Newest</span></div>
      </figure>}
      <DataTable label="Inference records" className="rb-table" rows={shown} rowKey={span => span.traceId} columns={columns}
        caption={all || spans.length <= SHOWN ? `All ${fmtCount(spans.length)} received traces, newest first` : `Latest ${SHOWN} of ${fmtCount(spans.length)} received traces, newest first`} />
      {spans.length > SHOWN && <div className="rb-more"><button className="cfg-btn-text" type="button" aria-expanded={all} onClick={() => setAll(value => !value)}>{all ? `Show latest ${SHOWN}` : `Show all ${fmtCount(spans.length)} traces`}</button></div>}
      <p className="portal-context-note">Measured by the on-device gateway. Browser and relay time are excluded. Each bar is one reported latency; this is not an all-time benchmark.</p>
    </> : <p className="portal-empty">No inference traces have been received from this device. A chat message to the running model records one.</p>}
  </Panel>;
}

/* ---------- Actions & traces (stored action traces) ---------- */

/** Latest actions with filter chips; "View trace" opens the drawer (`?trace=`). */
export function ActionsPanel({ workspace, robot, now, openTraceId, onOpen }: { workspace: ConvoyWorkspace; robot: Robot; now: number | null; openTraceId: string | null; onOpen: (traceId: string) => void }) {
  const [filter, setFilter] = useState<TraceFilter>("all");
  const counts = traceCounts(workspace, robot.id);
  const all = tracesFor(workspace, robot.id);
  const traces = tracesFor(workspace, robot.id, filter);
  const zone = robot.clock?.zone ?? "UTC";
  const window = timeWindowLabel(all.map(trace => trace.at), robot.clock);
  const columns: Array<Column<ActionTrace>> = [
    { key: "at", header: `Time (${zone})`, cell: trace => fmtTime(trace.at, robot.clock) },
    { key: "instruction", header: "Instruction", wrap: true, cell: trace => <span className="rb-instr">{trace.instruction}</span>, detail: trace => trace.reference },
    { key: "decision", header: "Decision", cell: trace => trace.decision.kind === "skill" ? trace.decision.skill ?? <NotReported /> : "Declined", detail: trace => trace.decision.kind === "skill" ? trace.decision.params : trace.decision.reason },
    { key: "path", header: "Path", cell: trace => <PathChip path={trace.path} />, detail: trace => trace.route },
    { key: "planner", header: "Planner", numeric: true, cell: trace => trace.plannerMs === null ? <NotReported /> : fmtMs(trace.plannerMs) },
    { key: "policy", header: "Policy p50 / p95", numeric: true, cell: trace => trace.policyMs ? policyLabel(trace.policyMs) : <NotReported>No policy call</NotReported>, detail: trace => trace.policyNote },
    { key: "outcome", header: "Outcome", cell: trace => <OutcomeBadge outcome={trace.outcome} />, detail: trace => trace.outcomeNote },
    { key: "duration", header: "Duration", numeric: true, cell: trace => trace.durationS === null ? <NotReported /> : fmtSeconds(trace.durationS) },
    { key: "open", header: "Trace", srOnly: true, cell: trace => <button className="portal-table-link" type="button" data-trace-button={trace.id} onClick={() => onOpen(trace.id)}>View trace<span className="cfg-sr"> · {trace.instruction}</span></button> },
  ];
  return <Panel eyebrow={window ? `${window} · device clock` : "Device clock"} title="Latest actions" titleId="rb-actions-title"
    action={all.length ? <FilterChips label="Filter actions" value={filter} onChange={setFilter} options={TRACE_FILTERS.map(id => ({ id, label: TRACE_FILTER_LABEL[id], count: counts[id] }))} /> : undefined}>
    {all.length ? <>
      <p className="rb-intro">Newest first. Planner is the plan time on the edge. Policy is on-device inference on the Edge and Fallback paths and the chunk round trip on the Cloud path.</p>
      <DataTable label="Actions and traces" className="rb-table rb-sr-caption" rows={traces} rowKey={trace => trace.id} rowClass={trace => trace.id === openTraceId ? "cfg-tr--current" : undefined} columns={columns} empty={TRACE_EMPTY[filter]}
        caption={`Actions on ${robot.name}, newest first${filter === "all" ? "" : `, ${TRACE_FILTER_LABEL[filter].toLowerCase()} only`}`} />
      <p className="portal-context-note rb-prov"><ProvenanceSet items={all} now={now} /> {all.every(trace => trace.provenance.kind === "sample") ? "Actions prepared for discussion; they are not measurements." : "Stored action traces for this robot."}</p>
    </> : <EmptyState icon="info" title={`No actions are recorded for ${robot.name}.`} text="Action traces appear here when the workspace holds them for this robot." />}
  </Panel>;
}

/* ---------- Logs ---------- */

/** Log lines stored in the workspace for this robot, on its device clock. */
export function LogsPanel({ workspace, robot, now }: { workspace: ConvoyWorkspace; robot: Robot; now: number | null }) {
  const lines = logsFor(workspace, { robotId: robot.id });
  const window = timeWindowLabel(lines.map(line => line.at), robot.clock);
  const empty = robot.deviceId
    ? `No log lines are stored for ${robot.name}. The device connection does not expose device or runtime logs.`
    : `No log lines are stored for ${robot.name}.`;
  return <Panel eyebrow={window ? `${window} · device clock` : "Device clock"} title={robot.deviceId ? "Device and runtime logs" : "Robot and model logs"} titleId="rb-logs-title">
    <LogPanel lines={lines} label={`${robot.name} logs`} clock={robot.clock} sourceLabel={line => `${line.source} · ${robot.name}`} empty={empty} />
    {lines.length > 0 && <p className="portal-context-note rb-prov"><ProvenanceSet items={lines} now={now} /> {lines.every(line => line.provenance.kind === "sample") ? "Log lines prepared for discussion; they are not device output." : "Log lines stored in the workspace for this robot."}</p>}
  </Panel>;
}
