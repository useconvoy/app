"use client";

import { fmtCount, fmtFixed, fmtNumber } from "@/lib/configurations/format";
import { seedText } from "@/lib/configurations/slices";
import type { SliceSummary } from "@/lib/configurations/slices";
import { Missing } from "../Badges";
import { DataTable, type Column } from "../DataTable";

interface Measure { key: string; label: string; title: string; cell: (summary: SliceSummary) => string | null }

const ms = (value: number | null | undefined) => value === null || value === undefined ? null : `${fmtNumber(value, 0)} ms`;
const counted = (value: number | null | undefined) => value === null || value === undefined ? null : fmtCount(value);

/** The rows: each slice's results side by side, from its own episodes only. */
const MEASURES: readonly Measure[] = [
  { key: "success", label: "Success", title: "Episodes that finished the task, of the slice's episodes", cell: ({ slice }) => `${slice.successes}/${slice.episodes.length} · ${fmtFixed(slice.successes / slice.episodes.length * 100, 0)} %` },
  { key: "seeds", label: "Seeds", title: "The slice's seeds", cell: ({ slice }) => seedText(slice.seeds) },
  { key: "placed", label: "Pills placed", title: "Pills in the bottle of the pills the episodes had (pills_placed of pills_total)", cell: ({ placed }) => placed && `${fmtCount(placed.placed)}/${fmtCount(placed.total)}` },
  { key: "failed", label: "Failed decisions", title: "Decisions with no usable reply after their retries", cell: ({ failedDecisions }) => counted(failedDecisions) },
  { key: "calls", label: "Planner calls", title: "Calls to the model: valid + refused + device or transport failures", cell: ({ calls }) => counted(calls?.calls) },
  { key: "refused", label: "Refused by the executive", title: "The model replied, the executive refused it: invalid choice, invalid JSON or invalid schema", cell: ({ calls }) => counted(calls?.refused.calls) },
  { key: "failures", label: "Device or transport failures", title: "No reply came back: device error, HTTP or transport error, or timeout", cell: ({ calls }) => counted(calls?.failed.calls) },
  { key: "device50", label: "On device p50", title: "Median of the episodes' own p50 on the device", cell: ({ latency }) => ms(latency?.p50.deviceMs) },
  { key: "device95", label: "On device p95", title: "Median of the episodes' own p95 on the device", cell: ({ latency }) => ms(latency?.p95.deviceMs) },
  { key: "e2e50", label: "End to end p50", title: "Median of the episodes' own end-to-end p50: what the robot waited for a decision", cell: ({ latency }) => ms(latency?.p50.e2eMs) },
  { key: "e2e95", label: "End to end p95", title: "Median of the episodes' own end-to-end p95", cell: ({ latency }) => ms(latency?.p95.e2eMs) },
  { key: "steps", label: "Median steps", title: "Median steps per episode", cell: ({ medianSteps }) => medianSteps === null ? null : fmtCount(Math.round(medianSteps)) },
  { key: "time", label: "Median time", title: "Median episode time", cell: ({ medianSeconds }) => medianSeconds === null ? null : `${fmtFixed(medianSeconds, 1)} s` },
];

/**
 * A sliced eval's slices side by side (one column each), so no figure pools them: success, items
 * placed, failed decisions, planner calls by result (refused apart from device or transport
 * failures, a 0 shown as 0), latency per decision and episode time. A row no slice reports is left out.
 */
export function SliceComparison({ summaries }: { summaries: readonly SliceSummary[] }) {
  const rows = MEASURES.filter(measure => summaries.some(summary => measure.cell(summary) !== null));
  // Episode time on one clock names it; different clocks are named per slice, never mixed in one label.
  const clocks = new Set(summaries.map(summary => summary.clock));
  const clock = clocks.size === 1 ? [...clocks][0] : null;
  const label = (measure: Measure) => measure.key === "time" && clock ? `Median time, ${clock === "wall" ? "wall clock" : "simulated"}` : measure.label;
  const columns: Array<Column<Measure>> = [
    { key: "measure", header: "Measure", srOnly: true, cell: measure => <span title={measure.title}>{label(measure)}</span> },
    ...summaries.map((summary): Column<Measure> => ({
      key: summary.slice.key, header: summary.slice.label, numeric: true,
      cell: measure => {
        const value = measure.cell(summary);
        if (value === null) return <Missing />;
        return measure.key === "time" && !clock && summary.clock ? `${value} ${summary.clock === "wall" ? "wall" : "sim"}` : value;
      },
    })),
  ];
  return <div className="cv-compare"><DataTable label="Slices" columns={columns} rows={rows} rowKey={measure => measure.key} /></div>;
}
