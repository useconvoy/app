"use client";

import { useId } from "react";
import type { PortalInference, PortalSnapshot, PortalTelemetry } from "@/lib/portal/types";
import { Badge } from "./Portal";
import { number, TelemetryChart, timestamp } from "./TelemetryChart";

export function Panel({ eyebrow, title, children, action, className = "" }: { eyebrow?: string; title: string; children: React.ReactNode; action?: React.ReactNode; className?: string }) {
  return <section className={`portal-panel ${className}`}><div className="portal-panel-heading"><div>{eyebrow && <p className="portal-eyebrow">{eyebrow}</p>}<h2>{title}</h2></div>{action}</div>{children}</section>;
}
export function Fact({ label, value, detail }: { label: string; value: React.ReactNode; detail?: React.ReactNode }) { return <div className="portal-fact"><dt>{label}</dt><dd>{value}{detail && <span className="portal-fact-detail">{detail}</span>}</dd></div>; }
function valueUnit(value: number | null | undefined, unit: string, digits = 1) { return value == null ? "Not reported" : `${number(value, digits)} ${unit}`; }
function elapsed(at: string | null, now: number) { if (!at || !now || !Number.isFinite(Date.parse(at))) return "Not reported"; const sec = Math.max(0, Math.floor((now - Date.parse(at)) / 1000)); return sec < 60 ? `${sec}s ago` : sec < 3600 ? `${Math.floor(sec / 60)}m ago` : `${Math.floor(sec / 3600)}h ago`; }
const metrics = [
  { key: "cpu_pct", label: "CPU utilization", unit: "%" },
  { key: "mem_available_mb", label: "Memory available", unit: "MiB" },
  { key: "temp_max_c", label: "Temperature", unit: "°C" },
  { key: "power_w", label: "Power", unit: "W" },
] as const;

export function Device({ snapshot: s, now, onChat }: { snapshot: PortalSnapshot; now: number; onChat: () => void }) {
  const sample = s.latest_telemetry;
  const threshold = "telemetry_stale_after_s" in s && typeof s.telemetry_stale_after_s === "number" ? s.telemetry_stale_after_s : null;
  const sampleStale = !!(threshold !== null && sample?.ts && now - Date.parse(sample.ts) > threshold * 1000);
  const times = s.telemetry.map((t) => t.ts).filter((t): t is string => !!t && Number.isFinite(Date.parse(t))).sort();
  return <>
    <section className="portal-device-board" aria-labelledby="running-model-heading"><div className="portal-device-drawing" aria-hidden="true"><svg viewBox="0 0 180 138"><rect x="30" y="25" width="120" height="88" rx="2" /><rect x="62" y="45" width="56" height="48" rx="2" /><path d="M75 55h30v28H75zM18 43h12m-12 13h12m-12 13h12m-12 13h12m-12 13h12m120-52h12m-12 13h12m-12 13h12m-12 13h12m-12 13h12M49 13v12m15-12v12m15-12v12m15-12v12m15-12v12m15-12v12M49 113v12m15-12v12m15-12v12m15-12v12m15-12v12m15-12v12" /><circle cx="42" cy="37" r="3" /><circle cx="138" cy="101" r="3" /></svg><span>On-device runtime</span></div>
      <div className="portal-device-model"><p className="portal-eyebrow">Running release</p><h2 id="running-model-heading">{s.release?.name ?? "No release reported"}</h2><p className="portal-model-repo">{s.release?.model_repo ?? "Model repository not reported"}</p><div className="portal-inline-facts"><Badge tone={s.chat.eligible ? "success" : "warning"}>{s.chat.eligible ? "Ready for inference" : "Inference unavailable"}</Badge><span>{s.device.runtime_state ?? "Runtime state not reported"}</span></div></div>
      <button className="btn btn-primary" onClick={onChat}>Open chat <span aria-hidden="true">↗</span></button>
    </section>
    {!s.chat.eligible && <p className="portal-context-note">{s.chat.reason ?? "The device is not currently ready to accept inference requests."}</p>}
    <dl className="portal-health-strip"><Fact label="Device contact" value={elapsed(s.device.live_at, now)} detail={timestamp(s.device.live_at)} /><Fact label="Observed health" value={s.device.observed_health ?? "Not reported"} detail={`Observed ${timestamp(s.device.observed_at)}`} /><Fact label="Latest telemetry" value={sample?.ts ? elapsed(sample.ts, now) : "Not reported"} detail={sampleStale ? "Sample is stale" : timestamp(sample?.ts)} /></dl>
    <div className="portal-section-label"><h2>Device telemetry</h2><span>{sampleStale ? "Last known measurements · stale" : "Latest reported measurements"}</span></div>
    <div className="portal-metric-grid">{metrics.map((metric) => <section className="portal-metric-card" key={metric.key}><h3>{metric.label}</h3><div className="portal-metric-value">{number(sample?.[metric.key])}{sample?.[metric.key] != null && <span>{metric.unit}</span>}</div><TelemetryChart title={metric.label} unit={metric.unit} points={s.telemetry.map((t) => ({ at: t.ts ?? "", value: t[metric.key] }))} maxGapMs={threshold === null ? 30000 : threshold * 1000} /></section>)}</div>
    <p className="portal-context-note">{times.length ? `${timestamp(times[0])} – ${timestamp(times.at(-1))}. ` : "No telemetry samples received. "}Dots represent received samples. Missing values and gaps longer than {threshold === null ? "30 seconds" : `${threshold} seconds`} are left open. {sample?.clock_confidence ? `Device clock: ${sample.clock_confidence}.` : "Device clock confidence not reported."}</p>
    <details className="portal-details"><summary>Inspect telemetry samples <span>{s.telemetry.length} received</span></summary><TelemetryTable samples={s.telemetry} /></details>
    <div className="portal-two-column"><Panel title="Model & runtime" eyebrow="Active release"><dl className="portal-facts"><Fact label="Model file" value={s.release?.model_file ?? "Not reported"} /><Fact label="Runtime" value={[s.release?.runtime_name, s.release?.runtime_backend].filter(Boolean).join(" · ") || "Not reported"} /><Fact label="Context window" value={valueUnit(s.release?.context_window, "tokens", 0)} /><Fact label="Output limit" value={valueUnit(s.release?.output_limit, "tokens", 0)} /></dl></Panel><Panel title="Device identity" eyebrow="Reported state"><dl className="portal-facts"><Fact label="Device ID" value={s.device.id} /><Fact label="Agent version" value={s.device.agent_version ?? "Not reported"} /><Fact label="Release ID" value={s.device.observed_active_release_id ?? "Not reported"} />{sample?.gpu_pct != null && <Fact label="GPU utilization" value={valueUnit(sample.gpu_pct, "%")} />}<Fact label="Total memory" value={valueUnit(sample?.mem_total_mb, "MiB", 0)} /></dl></Panel></div>
  </>;
}
function TelemetryTable({ samples }: { samples: PortalTelemetry[] }) { return <div className="portal-table-scroll" tabIndex={0} aria-label="Telemetry samples, scroll horizontally"><table className="portal-table"><caption>Exact telemetry samples, newest first</caption><thead><tr><th scope="col">Sample time</th>{metrics.map((m) => <th scope="col" key={m.key}>{m.label} ({m.unit})</th>)}<th scope="col">Clock</th></tr></thead><tbody>{samples.toReversed().map((sample, i) => <tr key={`${sample.ts}-${i}`}><th scope="row">{timestamp(sample.ts)}</th>{metrics.map((m) => <td key={m.key}>{number(sample[m.key])}</td>)}<td>{sample.clock_confidence ?? "Not reported"}</td></tr>)}</tbody></table>{!samples.length && <p className="portal-empty">No telemetry samples received.</p>}</div>; }

export function Usage({ snapshot: s }: { snapshot: PortalSnapshot }) {
  const m = s.usage.metrics;
  return <>
    <div className="portal-range-banner"><span className="portal-eyebrow">Accounting window / UTC</span><strong>{s.usage.from} <span aria-hidden="true">→</span> {s.usage.to}</strong><span>{s.device.name} only</span></div>
    <div className="portal-metric-grid portal-usage-counts">{[{ key: "inference_requests", label: "Inference requests" }, { key: "tokens_in", label: "Input tokens" }, { key: "tokens_out", label: "Output tokens" }].map((metric) => <section className="portal-metric-card" key={metric.key}><h2>{metric.label}</h2><p className="portal-metric-value">{number(m?.[metric.key], 0)}</p><p className="portal-context-note">Device-reported counter</p></section>)}</div>
    <Panel title="Measured resource time" eyebrow="Physical device"><p className="portal-panel-intro">Each clock measures a different activity. These intervals overlap.</p><dl className="portal-resource-times"><Fact label="Inference busy" value={valueUnit(m?.inference_minutes, "min", 2)} detail="Time the inference slot was occupied" /><Fact label="Runtime running" value={valueUnit(m?.runtime_up_minutes, "min", 2)} detail="Time the model runtime process was running" /><Fact label="Agent running" value={valueUnit(m?.agent_up_minutes, "min", 2)} detail="Time the device agent process was running" /></dl></Panel>
    <div className="portal-two-column"><Panel title="Accounting coverage"><dl className="portal-facts"><Fact label="Unknown coverage" value={valueUnit(m?.unknown_coverage_s, "s", 2)} detail="Time explicitly reported as unobserved after an agent restart" /></dl></Panel><Panel title="How to read this window"><div className="portal-prose"><p>Counters include usage records received from this device for the displayed UTC dates. Buffered records can arrive later and revise earlier totals.</p><p>Unknown coverage is not counted as measured activity. No report for a metric appears as “Not reported”; a reported zero remains zero.</p><p>Usage includes all recorded inference on this device, including requests outside this browser session.</p></div></Panel></div>
  </>;
}
function median(values: number[]) { const sorted = values.toSorted((a, b) => a - b); const middle = Math.floor(sorted.length / 2); return !sorted.length ? null : sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2; }
function percentile(values: number[], p: number) { return values.length ? values.toSorted((a, b) => a - b)[Math.max(0, Math.ceil(values.length * p) - 1)] : null; }
export function Traces({ snapshot: s, selectedId, onSelect }: { snapshot: PortalSnapshot; selectedId: string | null; onSelect: (id: string | null) => void }) {
  const rows = s.recent_inference;
  const measured = rows.map((r) => r.latency_ms).filter((v): v is number => v !== null && Number.isFinite(v));
  const selected = rows.find((r) => r.trace_id === selectedId);
  return <>
    <div className="portal-trace-summary"><div><p className="portal-eyebrow">Latest received inference traces</p><p>{rows.length} received traces from {s.device.name}. This sample is a bounded view of received evidence.</p></div><dl><Fact label="Measured latency sample" value={`${measured.length} requests`} /><Fact label="Median" value={valueUnit(median(measured), "ms")} /><Fact label="p95 of this sample" value={valueUnit(percentile(measured, .95), "ms")} /></dl></div>
    {!!rows.length && <Panel title="On-device request latency" eyebrow="Received inference sample"><LatencyChart rows={rows} /><p className="portal-context-note">Measured by the on-device gateway. Browser and relay time are excluded. Each bar is one reported latency; this is not an all-time benchmark.</p></Panel>}
    {selectedId && <Panel title="Trace detail" action={<button className="portal-text-button" onClick={() => onSelect(null)}>Close</button>}><p className="portal-trace-id">{selectedId}</p>{selected ? <dl className="portal-trace-facts"><Fact label="Started" value={timestamp(selected.start_ts)} /><Fact label="Status" value={selected.status ?? "Not reported"} /><Fact label="On-device latency" value={valueUnit(selected.latency_ms, "ms")} /><Fact label="Time to first token" value={valueUnit(selected.ttft_ms, "ms")} /><Fact label="Queue time" value={valueUnit(selected.queue_ms, "ms")} /><Fact label="Input / output tokens" value={`${number(selected.tokens_in, 0)} / ${number(selected.tokens_out, 0)}`} /><Fact label="Reported throughput" value={valueUnit(selected.tok_s, "tokens/s")} /></dl> : <p className="portal-panel-intro">This trace is not in the latest received sample. Its upload may still be pending, or newer requests may have moved it outside this window. Refresh to check again.</p>}</Panel>}
    <div className="portal-panel"><div className="portal-panel-heading"><h2>Inference records</h2><Badge>{rows.length} received</Badge></div><div className="portal-table-scroll" tabIndex={0} aria-label="Inference records, scroll horizontally"><table className="portal-table"><caption>Latest received inference traces for {s.device.name}</caption><thead><tr><th scope="col">Started</th><th scope="col">Trace</th><th scope="col">Status</th><th scope="col">Device latency</th><th scope="col">Input tokens</th><th scope="col">Output tokens</th></tr></thead><tbody>{rows.map((r) => <tr key={r.trace_id}><td>{timestamp(r.start_ts)}</td><th scope="row"><button className="portal-table-link" onClick={() => onSelect(r.trace_id)}>{r.trace_id}</button></th><td>{r.status ?? "Not reported"}</td><td>{valueUnit(r.latency_ms, "ms")}</td><td>{number(r.tokens_in, 0)}</td><td>{number(r.tokens_out, 0)}</td></tr>)}</tbody></table>{!rows.length && <p className="portal-empty">No inference traces have been received for this device.</p>}</div></div>
  </>;
}
function LatencyChart({ rows }: { rows: PortalInference[] }) {
  const id = useId();
  const measured = rows.toReversed().filter((r): r is PortalInference & { latency_ms: number } => r.latency_ms !== null && Number.isFinite(r.latency_ms));
  if (!measured.length) return <p className="portal-empty">No measured latency in this sample.</p>;
  const high = Math.max(1, ...measured.map((r) => r.latency_ms));
  const width = 640 / measured.length;
  return <figure className="portal-latency-chart"><svg viewBox="0 0 700 150" role="img" aria-labelledby={`${id}-title ${id}-desc`}><title id={`${id}-title`}>Measured latency per received inference</title><desc id={`${id}-desc`}>{measured.length} requests, oldest to newest, scaled from zero to {number(high)} milliseconds. Exact values are listed in the inference records table.</desc><path d="M45 120H685" className="portal-chart-axis" /><text x="0" y="18">{number(high, 0)}</text><text x="24" y="123">0</text>{measured.map((r, index) => <rect key={r.trace_id} x={45 + index * width + 1} y={120 - r.latency_ms / high * 105} width={Math.max(1, width - 2)} height={Math.max(1, r.latency_ms / high * 105)} className="portal-latency-bar" />)}</svg><figcaption><span>Older received requests</span><span>Newer received requests</span></figcaption></figure>;
}
