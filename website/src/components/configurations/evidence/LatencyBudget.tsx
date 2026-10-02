"use client";

import type { ReactNode } from "react";
import { EVIDENCE_DEFINITIONS, latencyScale } from "@/lib/configurations/evidence";
import type { LatencyBreakdown, LatencyBudget } from "@/lib/configurations/evidence";
import { fmtCount, fmtFixed, fmtNumber } from "@/lib/configurations/format";
import type { LatencyTarget } from "@/lib/configurations/types";
import { Missing } from "../Badges";
import { EvidenceCard, type Fact } from "./EvidenceCard";

type Percentile = "p50" | "p95";
type SegmentKind = "prefill" | "decode" | "device" | "network";
interface Segment { kind: SegmentKind; ms: number }

const SEGMENT_LABEL: Record<SegmentKind, string> = { prefill: "Prefill", decode: "Decode", device: "On device", network: "Network / relay" };
const SEGMENT_TITLE: Record<SegmentKind, string> = { prefill: EVIDENCE_DEFINITIONS.prefill, decode: EVIDENCE_DEFINITIONS.decode, device: "Prefill + decode, not split", network: EVIDENCE_DEFINITIONS.network };
const ms = (value: number) => `${fmtNumber(value, 0)} ms`;
/** Axis ticks on one unit (seconds from a 1 s axis up, else ms), the unit on the last tick only: "0 1 2 3 4 s", "0 100 200 300 ms". */
function tickLabels(ticks: readonly number[], maxMs: number): string[] {
  const inSeconds = maxMs >= 1000;
  return ticks.map((value, i) => {
    const number = inSeconds ? fmtNumber(value / 1000, 1) : fmtNumber(value, 0);
    return i === ticks.length - 1 && value > 0 ? `${number} ${inSeconds ? "s" : "ms"}` : number;
  });
}
/** "17.8 s", "1.45 s": two decimals under 10 s. */
const seconds = (value: number) => `${fmtFixed(value / 1000, value < 10_000 ? 2 : 1)} s`;
const pct = (value: number, max: number) => `${Math.min(100, Math.max(0, value / max * 100))}%`;

/**
 * The bar's segments: prefill and decode when both are reported, else the on-device time unsplit; then
 * network / relay when reported. Null without an on-device time (nothing to draw to scale).
 */
function segments(row: LatencyBreakdown): Segment[] | null {
  if (row.deviceMs === null) return null;
  const device: Segment[] = row.prefillMs !== null && row.decodeMs !== null
    ? [{ kind: "prefill", ms: row.prefillMs }, { kind: "decode", ms: row.decodeMs }]
    : [{ kind: "device", ms: row.deviceMs }];
  return row.networkMs !== null ? [...device, { kind: "network", ms: row.networkMs }] : device;
}
/** What the bar adds up to: the end-to-end round trip when split, else the on-device time. */
const totalOf = (row: LatencyBreakdown) => row.e2eMs !== null && row.networkMs !== null ? row.e2eMs : row.deviceMs;

function describe(budget: LatencyBudget, rows: readonly Percentile[], targets: readonly LatencyTarget[]): string {
  const parts = rows.map(name => {
    const row = budget[name], drawn = segments(row), total = totalOf(row);
    if (!drawn || total === null) return `${name} not reported`;
    return `${name} ${ms(total)} ${row.networkMs !== null ? "end to end" : "on device"}: ${drawn.map(segment => `${SEGMENT_LABEL[segment.kind].toLowerCase()} ${ms(segment.ms)}`).join(", ")}`;
  });
  const references = targets.length ? ` References: ${targets.map(target => `${target.label} ${ms(target.ms)}`).join(", ")}.` : "";
  return `Latency per ${budget.source === "eval" ? "planner decision" : "request"}, linear scale from 0. ${parts.join(". ")}.${references}`;
}

/**
 * Stacked bars of one decision's time (p50, and p95 unless compact) on one linear axis from 0, and the
 * configuration's latency targets as labelled reference rows on the same axis: every length is to
 * scale, and each target keeps its own row, so a 60 ms target stays legible beside a 2.7 s decision.
 * One grid: names sized to the longest label, a shared track column, values right-aligned.
 */
function BudgetChart({ budget, rows, targets }: { budget: LatencyBudget; rows: readonly Percentile[]; targets: readonly LatencyTarget[] }) {
  const scale = latencyScale([...rows.map(name => totalOf(budget[name])), ...targets.map(target => target.ms)]);
  const labels = tickLabels(scale.ticks, scale.maxMs);
  const count = rows.length + targets.length;
  const at = (row: number) => ({ gridRow: row + 2 });
  return <div className="cv-lb" role="img" aria-label={describe(budget, rows, targets)}>
    <span className="cv-lb__ticks" style={{ gridRow: 1 }}>{scale.ticks.map((value, i) => <span key={value} style={{ left: pct(value, scale.maxMs) }}>{labels[i]}</span>)}</span>
    <span className="cv-lb__grid" style={{ gridRow: `2 / span ${count}` }}>{scale.ticks.map(value => <i key={value} style={{ left: pct(value, scale.maxMs) }} />)}</span>
    {rows.map((name, i) => {
      const row = budget[name], drawn = segments(row), total = totalOf(row);
      const sum = drawn?.reduce((value, segment) => value + segment.ms, 0) ?? 0;
      return [
        <span key={`${name}-name`} className="cv-lb__name" style={at(i)}>{name}</span>,
        <span key={`${name}-track`} className="cv-lb__track" style={at(i)}>
          {drawn && sum > 0 && <span className="cv-lb__bar" style={{ width: pct(sum, scale.maxMs) }}>
            {drawn.map(segment => <span key={segment.kind} className={`cv-lb__seg cv-lb__seg--${segment.kind}`} style={{ width: pct(segment.ms, sum) }}
              title={`${name} · ${SEGMENT_LABEL[segment.kind]} ${ms(segment.ms)}`} />)}
          </span>}
        </span>,
        <span key={`${name}-value`} className="cv-lb__value" style={at(i)}>{total === null ? "–" : ms(total)}</span>,
      ];
    })}
    {targets.map((target, i) => [
      <span key={`${target.label}-name`} className="cv-lb__name cv-lb__name--target" style={at(rows.length + i)} title={target.label}>{target.label}</span>,
      <span key={`${target.label}-track`} className="cv-lb__track cv-lb__track--target" style={at(rows.length + i)}>
        <span className="cv-lb__ref" style={{ width: pct(target.ms, scale.maxMs) }} title={`${target.label} ${ms(target.ms)}`} />
      </span>,
      <span key={`${target.label}-value`} className="cv-lb__value cv-lb__value--target" style={at(rows.length + i)}>{ms(target.ms)}</span>,
    ])}
  </div>;
}

const cell = (value: number | null) => value === null ? <Missing /> : ms(value);
const swatch = (kind: SegmentKind) => <i className={`cv-lb__swatch cv-lb__seg--${kind}`} aria-hidden="true" />;

/** The chart's values as a table (its accessible twin): each part, the on-device subtotal and the total, per percentile. */
function BudgetTable({ budget, rows }: { budget: LatencyBudget; rows: readonly Percentile[] }) {
  const lines: Array<{ key: string; label: ReactNode; title: string; value: (row: LatencyBreakdown) => number | null; total?: boolean }> = [
    { key: "prefill", label: <>{swatch("prefill")}Prefill</>, title: SEGMENT_TITLE.prefill, value: row => row.prefillMs },
    { key: "decode", label: <>{swatch("decode")}Decode</>, title: SEGMENT_TITLE.decode, value: row => row.decodeMs },
    { key: "device", label: <>{swatch("device")}On device</>, title: "Prefill + decode", value: row => row.deviceMs },
    { key: "network", label: <>{swatch("network")}Network / relay</>, title: SEGMENT_TITLE.network, value: row => row.networkMs },
    { key: "e2e", label: "End to end", title: "What the robot waited for the decision", value: row => row.e2eMs, total: true },
  ];
  return <table className="cv-lb__table">
    <caption className="cv-sr">Latency per {budget.source === "eval" ? "planner decision" : "request"}, milliseconds</caption>
    <thead><tr><th scope="col"><span className="cv-sr">Part</span></th>{rows.map(name => <th key={name} scope="col">{name}</th>)}</tr></thead>
    <tbody>{lines.map(line => <tr key={line.key} className={line.total ? "cv-lb__total" : undefined}>
      <th scope="row" title={line.title}>{line.label}</th>
      {rows.map(name => <td key={name}>{cell(line.value(budget[name]))}</td>)}
    </tr>)}</tbody>
  </table>;
}

/** p50 values as one wrapped line of swatches (the compact panel's legend and table). */
function BudgetLegend({ budget }: { budget: LatencyBudget }) {
  const row = budget.p50;
  const items: Array<[SegmentKind, number | null]> = row.prefillMs !== null && row.decodeMs !== null ? [["prefill", row.prefillMs], ["decode", row.decodeMs]] : [["device", row.deviceMs]];
  items.push(["network", row.networkMs]);
  return <ul className="cv-lb__legend" aria-label="p50 by part">{items.map(([kind, value]) => <li key={kind} title={SEGMENT_TITLE[kind]}>{swatch(kind)}{SEGMENT_LABEL[kind]}<b>{value === null ? <Missing /> : ms(value)}</b></li>)}</ul>;
}

function facts(budget: LatencyBudget, compact: boolean): Fact[] {
  const tokens = (value: number | null) => value === null ? "–" : fmtCount(value);
  const items: Fact[] = [
    { label: "Tokens (p50)", value: budget.tokensIn === null && budget.tokensOut === null ? <Missing /> : `${tokens(budget.tokensIn)} in · ${tokens(budget.tokensOut)} out`, title: "Prompt in, reply out, per request" },
    { label: "Prefill share", value: budget.prefillShare === null ? <Missing /> : `${fmtFixed(budget.prefillShare * 100, 0)} % of on-device time`, title: "Prefill ÷ on-device time at p50" },
  ];
  if (!compact && budget.source === "eval") {
    items.push({ label: "Slowest episode p95", value: budget.worst ? `${seconds(budget.worst.e2eMs)} end to end${budget.worst.deviceMs === null ? "" : ` · ${seconds(budget.worst.deviceMs)} on device`}` : <Missing />, title: "The episode with the highest end-to-end p95, and its on-device p95" });
  }
  return items;
}

/**
 * Latency budget of one planner decision: where its time goes (prefill, decode, network / relay) at p50
 * and p95, against the configuration's latency targets, with tokens and the prefill share. Values are
 * measured: an eval's recorded per-episode metrics, or a live device's spans. `slice`: the slice the
 * episodes are, named first in the scope line. `compact`: p50 only, with `source` (a link to where the
 * values come from) in its foot.
 */
export function LatencyBudgetPanel({ budget, targets, slice, source, compact = false }: { budget: LatencyBudget; targets: readonly LatencyTarget[]; slice?: string; source?: ReactNode; compact?: boolean }) {
  const rows: Percentile[] = compact ? ["p50"] : ["p50", "p95"];
  const fromEval = budget.source === "eval";
  const scope = fromEval ? `median of ${fmtCount(budget.n)} ${budget.n === 1 ? "episode" : "episodes"}` : `${fmtCount(budget.n)} ${budget.n === 1 ? "request" : "requests"}`;
  const definitions = [
    { term: "Prefill", text: EVIDENCE_DEFINITIONS.prefill },
    { term: "Decode", text: EVIDENCE_DEFINITIONS.decode },
    { term: "Network / relay", text: EVIDENCE_DEFINITIONS.network },
    { term: "p50, p95", text: fromEval ? "Median across episodes of each episode's own percentile." : "Median and nearest-rank p95 of the latest requests; no end-to-end time." },
    { term: "Targets", text: targets.length ? "Declared on the configuration; same linear scale." : "None declared on the configuration." },
  ];
  return <EvidenceCard title="Latency budget" compact={compact} definitions={definitions} facts={facts(budget, compact)} note="Motor loop runs separately on the robot." source={source}
    provenance={fromEval ? "Measured: real calls to the model on the device, recorded per episode in this eval" : "Measured: the device's latest inference requests"}>
    <p className="cv-ev__sub">{slice ? `${slice} · per planner decision` : fromEval ? "Per planner decision" : "Per request, on the device"} · {compact ? "p50" : scope} · linear scale</p>
    <BudgetChart budget={budget} rows={rows} targets={targets} />
    {compact ? <BudgetLegend budget={budget} /> : <BudgetTable budget={budget} rows={rows} />}
  </EvidenceCard>;
}
