"use client";

import { useId, useState } from "react";
import { Badges, OUTCOME_LABEL, OutcomeBadge, PATH_LABEL, PathChip, ProvenanceBadge } from "@/components/configurations/Badges";
import { Legend } from "@/components/configurations/Charts";
import { Fact, FactList } from "@/components/configurations/Facts";
import { Drawer } from "@/components/configurations/Overlay";
import { Waterfall } from "@/components/configurations/Waterfall";
import { fmtDateTime, fmtMs, fmtSeconds, provenanceDetail } from "@/lib/configurations/format";
import { traceFactGroups, traceWaterfalls } from "@/lib/configurations/robot";
import type { ActionTrace, ConfigRevision, Robot } from "@/lib/configurations/types";

function CopyTraceId({ value }: { value: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try { await navigator.clipboard.writeText(value); setState("copied"); }
    catch { setState("failed"); }
  }
  return <span className="rb-copy">
    <button className="cfg-btn-text" type="button" onClick={() => void copy()}>{state === "copied" ? "Trace ID copied" : "Copy trace ID"}</button>
    <span className="rb-copy__status" role="status">{state === "failed" ? "Copying is blocked here; select the ID under the title." : ""}</span>
  </span>;
}

/**
 * The `?trace=` drawer: outcome, span waterfalls per group, inputs and outputs,
 * other stored fact groups, model versions, safety and provenance. An id that is
 * not one of this robot's traces opens the drawer with a plain not-found note.
 */
export function TraceDrawer({ traceId, trace, robot, revision, configurationName, now, onClose }: {
  traceId: string | null; trace: ActionTrace | null; robot: Robot; revision: ConfigRevision | null; configurationName: string; now: number | null; onClose: () => void;
}) {
  const id = useId();
  const open = traceId !== null;
  if (!trace) {
    return <Drawer open={open} onClose={onClose} closeLabel="Close trace" eyebrow={`Trace · ${robot.name}`} title="Trace not found"
      footer={<button className="btn btn-secondary cfg-btn" type="button" onClick={onClose}>Close</button>}>
      <p className="portal-empty">No action trace “{traceId}” is recorded for {robot.name}. The link may be for another robot, or the trace is no longer in the workspace.</p>
    </Drawer>;
  }
  const waterfalls = traceWaterfalls(trace);
  const groups = traceFactGroups(trace, { revision, configurationName });
  const kind = trace.provenance.kind;
  return <Drawer open={open} onClose={onClose} closeLabel="Close trace"
    eyebrow={`Trace · ${robot.name} · ${fmtDateTime(trace.at, robot.clock)} · device clock`} title={trace.instruction}
    subtitle={<p className="rb-traceid">{trace.id}{trace.reference ? ` · ${trace.reference}` : ""}</p>}
    footer={<><CopyTraceId value={trace.id} /><button className="btn btn-secondary cfg-btn" type="button" onClick={onClose}>Close</button></>}>
    <Badges><OutcomeBadge outcome={trace.outcome} /><PathChip path={trace.path} /><ProvenanceBadge provenance={trace.provenance} now={now} /></Badges>
    <dl className="portal-trace-facts">
      <Fact label="Outcome" value={OUTCOME_LABEL[trace.outcome]} detail={trace.outcomeNote ?? undefined} />
      <Fact label="Duration" value={fmtSeconds(trace.durationS)} detail={trace.plannerMs !== null ? `Planner ${fmtMs(trace.plannerMs)}` : undefined} />
      <Fact label="Path" value={PATH_LABEL[trace.path]} detail={trace.route} />
    </dl>
    {waterfalls.map(waterfall => <section className="rb-group" key={waterfall.id} aria-labelledby={`${id}-wf-${waterfall.id}`}>
      <div className="cfg-subhead">
        <h3 id={`${id}-wf-${waterfall.id}`}>{waterfall.title}</h3>
        <Legend items={waterfall.paths.map(path => path === "operator" ? { label: PATH_LABEL[path], key: "operator" as const } : { label: PATH_LABEL[path], series: path === "edge" || path === "cloud" || path === "fallback" ? path : undefined })} />
      </div>
      <Waterfall spans={waterfall.spans} unit={waterfall.unit} />
    </section>)}
    {groups.map((group, i) => <section className="rb-group" key={`${group.title}-${i}`} aria-labelledby={`${id}-group-${i}`}>
      <div className="cfg-subhead"><h3 id={`${id}-group-${i}`}>{group.title}</h3></div>
      <FactList facts={group.facts} className="rb-facts" />
    </section>)}
    <p className="portal-context-note rb-prov"><ProvenanceBadge provenance={trace.provenance} now={now} /> {kind === "sample" ? "Trace prepared for discussion; it is not a measurement." : kind === "recorded" ? `Recorded trace: ${provenanceDetail(trace.provenance)}.` : "Stored action trace."} Span times are from the observation, on the device clock.</p>
  </Drawer>;
}
