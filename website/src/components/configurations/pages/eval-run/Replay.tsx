"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { EpisodeReplay } from "@/components/console/EpisodeReplay";
import { sliceInfo, taskName } from "@/lib/configurations/client";
import {
  fmtDuration, keySteps, lastAtOrBefore, pathAt, pathSegments, pathTotals, replayClock, scrubMarkers, seriesWithin, windowAround, type PathSegment,
} from "@/lib/configurations/eval-run";
import { fmtCount, fmtFixed, fmtMs, fmtSeconds, fmtUnit, NOT_REPORTED, provenanceDetail, runLabel } from "@/lib/configurations/format";
import { ID_PATTERN } from "@/lib/configurations/types";
import type { ConfigRevision, Configuration, ConvoyWorkspace, EvalRun, EvalSuite, PathKind, Provenance, Rollout, RolloutOutcome, RolloutStep } from "@/lib/configurations/types";
import type { EvaluationRun } from "@/lib/platform/client";
import { NotReported, OutcomeBadge, PATH_LABEL, PathChip, ProvenanceBadge } from "../../Badges";
import { LegendKey, plotGeometry } from "../../Charts";
import { Fact } from "../../Facts";
import { Icon } from "../../Icons";
import { WorkspaceNotice, WorkspaceSourceNotice } from "../../Notice";
import { EmptyState, LoadingState } from "../../States";
import { usePlatformRead, useReducedMotion, type Remote } from "./hooks";
import { recordedProvenance } from "./RunSections";

/** What `?rollout=` points at for this run. */
export type ReplayTarget =
  | { kind: "sample"; id: string; rollout: Rollout }
  | { kind: "recorded"; id: string; episodeId: string; outcome: RolloutOutcome | null; provenance: Provenance; rollout: Rollout | null; seed: number | null; title: string }
  | { kind: "loading"; id: string }
  | { kind: "missing"; id: string };

/**
 * Resolves `?rollout=`: a stored rollout of this run (a real replay when it links an
 * `episodeId`), the run's own recorded episode, or a case episode of the run's
 * recorded evaluation. Anything else is not part of this run.
 */
export function resolveReplay(workspace: ConvoyWorkspace, run: EvalRun, suite: EvalSuite | null, id: string, evaluation: Remote<EvaluationRun>): ReplayTarget {
  const rollout = workspace.rollouts.find(item => item.id === id && item.runId === run.id) ?? null;
  if (rollout) {
    return rollout.episodeId
      ? { kind: "recorded", id, episodeId: rollout.episodeId, outcome: rollout.outcome, provenance: rollout.provenance, rollout, seed: rollout.seed, title: taskName(suite, rollout.taskId) }
      : { kind: "sample", id, rollout };
  }
  if (run.recordedEpisodeId === id) {
    const { successes, episodes } = run.counts;
    const outcome: RolloutOutcome | null = successes === null || episodes !== 1 ? null : successes === 1 ? "succeeded" : "failed";
    return { kind: "recorded", id, episodeId: id, outcome, provenance: run.provenance, rollout: null, seed: null, title: run.title };
  }
  if (run.recordedEvaluationId) {
    if (evaluation.status === "loading" || evaluation.status === "idle") return { kind: "loading", id };
    if (evaluation.status === "ready") {
      const data = evaluation.data;
      const reported = data.report?.cases.find(item => item.episode_id === id);
      const listed = data.cases.find(item => item.episode_id === id);
      if (reported || listed) {
        const outcome: RolloutOutcome | null = reported && reported.evidence_valid ? (reported.passed ? "succeeded" : "failed") : null;
        const seed = reported?.seed ?? listed?.seed ?? null;
        return { kind: "recorded", id, episodeId: id, outcome, provenance: recordedProvenance(data), rollout: null, seed, title: seed === null ? data.id : `Seed ${seed}` };
      }
    }
  }
  return { kind: "missing", id };
}

/**
 * The replay view (full width, as the mock): header with "Back to Run N" and
 * close, episode facts, then the sample placeholder replay or the recorded
 * camera journal. Escape closes it; focus starts on the title.
 */
export function ReplayView({ workspace, run, suite, configuration, pageConfigId, target, onClose, now }: {
  workspace: ConvoyWorkspace; run: EvalRun; suite: EvalSuite | null; configuration: Configuration | null; pageConfigId: string; target: ReplayTarget; onClose: () => void; now: number | null;
}) {
  const title = useRef<HTMLHeadingElement>(null);
  const close = useRef(onClose);
  useEffect(() => { close.current = onClose; }, [onClose]);
  useEffect(() => {
    window.scrollTo({ top: 0 });
    title.current?.focus({ preventScroll: true });
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape" || event.defaultPrevented || document.querySelector("[aria-modal='true']")) return;
      event.preventDefault();
      close.current();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);
  const revision = configuration?.revisions.find(item => item.rev === run.rev) ?? null;
  const back = `Back to ${runLabel(run)}`;
  const configName = configuration ? `${configuration.name} ${run.rev}` : `${run.configId} ${run.rev}`;
  let heading: ReactNode = target.id, badges: ReactNode = null, meta: ReactNode = null, body: ReactNode;
  if (target.kind === "sample") {
    const { rollout } = target;
    const clock = replayClock(rollout);
    heading = `${rollout.id} · ${taskName(suite, rollout.taskId)}`;
    badges = <><OutcomeBadge outcome={rollout.outcome} /><ProvenanceBadge provenance={rollout.provenance} now={now} /></>;
    meta = [runLabel(run), configName, rollout.sliceIds.length ? `slice ${rollout.sliceIds.map(id => sliceInfo(suite, id).name).join(", ")}` : null, `seed ${rollout.seed}`,
      `${fmtSeconds(rollout.durationS)} simulated, ${fmtCount(rollout.steps)} steps at ${fmtFixed(clock.rate, 0)} Hz`, run.simEngine ?? null].filter(Boolean).join(" · ");
    body = <SampleReplay rollout={rollout} suite={suite} revision={revision} now={now} />;
  } else if (target.kind === "recorded") {
    heading = `${target.episodeId} · ${target.title}`;
    badges = <>{target.outcome && <OutcomeBadge outcome={target.outcome} />}<ProvenanceBadge provenance={target.provenance} now={now} /></>;
    meta = [runLabel(run), configName, target.rollout?.sliceIds.length ? `slice ${target.rollout.sliceIds.map(id => sliceInfo(suite, id).name).join(", ")}` : null,
      target.seed !== null ? `seed ${target.seed}` : null, "Recorded camera journal from the control plane"].filter(Boolean).join(" · ");
    body = <RecordedReplay target={target} suite={suite} now={now} />;
  } else if (target.kind === "loading") {
    body = <LoadingState label="Reading the recorded evaluation…" />;
  } else {
    body = <EmptyState icon="info" title={`This rollout is not part of ${runLabel(run)}.`} text={`No stored rollout or recorded episode “${target.id}” belongs to this run. Use “${back}” to return to its results.`} />;
  }
  return <div className="ev-replay">
    <div className="portal-page-heading cfg-page-head">
      <div>
        <p className="portal-eyebrow">Rollout replay</p>
        <div className="cfg-title"><h1 ref={title} tabIndex={-1}>{heading}</h1>{badges}</div>
      </div>
      <div className="cfg-actions">
        <button className="btn btn-secondary cfg-btn" type="button" onClick={onClose}>{back}</button>
        <button className="cfg-icon-btn cfg-icon-btn--ghost" type="button" aria-label="Close replay" onClick={onClose}><Icon name="close" /></button>
      </div>
    </div>
    {meta && <p className="portal-updated">{meta}</p>}
    <WorkspaceSourceNotice />
    <WorkspaceNotice workspace={workspace} configId={pageConfigId} />
    {body}
  </div>;
}

/* ---------- sample rollout ---------- */

const SPEEDS = [0.5, 1, 2, 4] as const;
const share = (value: number) => `${Math.round(Math.min(Math.max(value, 0), 1) * 10000) / 100}%`;
const LATENCY_NOTE: Record<PathKind | "none", string> = {
  edge: "Decision or chunk on the edge device", cloud: "Observation to chunk, cloud round trip", fallback: "Chunk inference on the edge fallback policy", none: "Event, no model call",
};

/** Placeholder frame, scrubber with event markers, transport, step readout, event list, key steps and synced signal plots. */
function SampleReplay({ rollout, suite, revision, now }: { rollout: Rollout; suite: EvalSuite | null; revision: ConfigRevision | null; now: number | null }) {
  const clock = replayClock(rollout);
  const { steps, rate } = clock;
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<number>(1);
  const reduced = useReducedMotion();
  const keys = useMemo(() => keySteps(rollout), [rollout]);
  const segments = useMemo(() => pathSegments(rollout.stepDetail, rollout.durationS), [rollout]);
  useEffect(() => {
    if (!playing || step >= steps) return;
    const timer = window.setTimeout(() => {
      const next = reduced ? keys.find(key => key > step) ?? steps : Math.min(steps, step + Math.max(1, Math.round(rate * speed / 10)));
      setStep(next);
      if (next >= steps) setPlaying(false);
    }, reduced ? 1200 : 100);
    return () => window.clearTimeout(timer);
  }, [playing, step, steps, rate, speed, reduced, keys]);

  const atS = clock.atStep(step);
  const atEnd = step >= steps;
  const seekStep = (value: number) => { setPlaying(false); setStep(Math.min(steps, Math.max(0, Math.round(value)))); };
  const seek = (seconds: number) => seekStep(clock.stepAt(seconds));
  const toggle = () => { if (playing && !atEnd) { setPlaying(false); return; } if (atEnd) setStep(0); setPlaying(true); };

  const events = rollout.events.toSorted((a, b) => a.atS - b.atS);
  const currentEvent = lastAtOrBefore(events, atS);
  const markers = scrubMarkers(events, rollout.durationS);
  const rows = (rollout.stepDetail ?? []).toSorted((a, b) => a.step - b.step);
  const rowIndex = rows.reduce((found, row, i) => row.step <= step ? i : found, -1);
  const current: RolloutStep | null = rowIndex >= 0 ? rows[rowIndex] : null;
  const planned = rows.filter(row => row.planner !== null && row.step <= step).at(-1) ?? null;
  const path = pathAt(segments, atS);
  const around = windowAround(rows, Math.max(0, rowIndex));
  const fallback = segments.filter(segment => segment.path === "fallback");
  const totals = pathTotals(segments);
  const violations = rollout.violations.filter(item => item.atS <= atS + 1e-9);
  const slices = rollout.sliceIds.map(id => ({ id, ...sliceInfo(suite, id), description: suite?.sliceFamilies.flatMap(family => family.slices).find(slice => slice.id === id)?.description }));
  const reference = suite?.tasks.find(task => task.id === rollout.taskId)?.referenceMedianS ?? null;
  const cameras = revision?.robot.cameras ?? [];
  const camera = cameras[0]?.toLowerCase() ?? "camera";
  const inset = cameras[1]?.replace(/^\d+\s*×\s*/, "") ?? null;
  const twin = revision?.robot.simTwin?.name ?? "sim twin";
  const position = `${fmtFixed(atS)} s simulated · step ${fmtCount(step)} / ${fmtCount(steps)}`;
  const outcomeDetail = rollout.failureMode ?? (rollout.violations.length ? `${rollout.violations.length} safety ${rollout.violations.length === 1 ? "violation" : "violations"}: ${rollout.violations.map(item => item.checkId).join(", ")}` : rollout.reward !== null && rollout.reward !== undefined ? `Reward ${rollout.reward}` : null);

  return <>
    <dl className="portal-health-strip ev-strip" aria-label="Episode facts">
      <Fact label="Outcome" value={<OutcomeBadge outcome={rollout.outcome} />} detail={outcomeDetail} />
      <Fact label="Simulated time" value={fmtSeconds(rollout.durationS)} detail={`${fmtCount(rollout.steps)} steps at ${fmtFixed(rate, 0)} Hz${reference !== null ? ` · reference median for this task ${fmtSeconds(reference)}` : ""}`} />
      <Fact label="Wall-clock time" value={fmtDuration(rollout.wallS) ?? <NotReported />} detail="Offline lockstep on the sim runner; not a timing claim" />
      <Fact label="Seed and slice" value={`${rollout.seed} · ${slices.map(slice => slice.name).join(", ") || "No slice"}`} detail={slices.map(slice => slice.description).filter(Boolean).join("; ") || null} />
      <Fact label="Policy time by path" value={totals.length ? totals.map(total => <span key={total.path} className={`cfg-path cfg-path--${total.path}`}>{PATH_LABEL[total.path]} {fmtFixed(total.seconds)} s</span>) : <NotReported />}
        detail={totals.length ? `${rows.filter(row => row.planner !== null).length} planner ${rows.filter(row => row.planner !== null).length === 1 ? "decision" : "decisions"} on the edge` : "No key steps stored for this rollout"} />
      <Fact label="Evidence" value={<ProvenanceBadge provenance={rollout.provenance} now={now} />} detail={provenanceDetail(rollout.provenance)} />
    </dl>

    <div className="cfg-replay">
      <div>
        <div className="cfg-frame" role="img" aria-label={`Placeholder for the ${camera} frame at step ${fmtCount(step)} of ${fmtCount(steps)}${inset ? `, with a ${inset} inset` : ""}`}>
          <span className="cfg-frame__corner">{rollout.provenance.kind === "sample" ? "Sample rollout" : "Rollout"} · {twin}</span>
          {path && <span className="ev-frame-tags"><PathChip path={path} /></span>}
          <p className="cfg-frame__label">Camera frame · {camera} · step {fmtCount(step)} / {fmtCount(steps)}<span>Placeholder · a recorded replay shows the camera frame here</span></p>
          {inset && <span className="ev-inset">{inset}<br />step {fmtCount(step)}</span>}
        </div>
        <div className="cfg-scrub ev-scrub">
          <div className="cfg-scrub__rail" aria-hidden="true">
            <span className="cfg-scrub__fill" style={{ width: share(step / steps) }} />
            {fallback.map(segment => <span key={segment.from} className="ev-band" style={{ left: share(segment.from / rollout.durationS), width: share((segment.to - segment.from) / rollout.durationS) }} />)}
            <span className="cfg-scrub__head" style={{ left: share(step / steps) }} />
          </div>
          <input className="ev-scrub__input" type="range" min={0} max={steps} step={1} value={step} aria-label="Replay position"
            aria-valuetext={`Step ${fmtCount(step)} of ${fmtCount(steps)}, ${fmtFixed(atS)} s simulated`} onChange={event => seekStep(Number(event.target.value))} />
          {markers.map(marker => <button key={`${marker.atS}-${marker.label}`} type="button" style={{ left: share(marker.share) }}
            className={`cfg-scrub__mark${marker.tone === "warning" ? " cfg-scrub__mark--warn" : marker.tone === "good" ? " cfg-scrub__mark--good" : ""}${marker.edge === "end" ? " cfg-scrub__mark--end" : marker.edge === "start" ? " ev-mark--start" : ""}`}
            aria-label={marker.events.map(event => `${event.label} at ${fmtFixed(event.atS)} s`).join("; ")} onClick={() => seek(marker.atS)}>{marker.labelled && <span>{marker.label}</span>}</button>)}
          <div className="cfg-scrub__scale" aria-hidden="true"><span>0 s</span>{fallback.length > 0 && <span className="cfg-inline"><i className="ev-key-shade" />Fallback {fallback.map(segment => `${fmtFixed(segment.from)}–${fmtFixed(segment.to)} s`).join(", ")}</span>}<span>{fmtSeconds(rollout.durationS)} simulated</span></div>
        </div>
        <div className="cfg-transport">
          <button className="cfg-icon-btn" type="button" aria-label="Previous step" aria-disabled={step === 0} onClick={() => { if (step > 0) seekStep(step - 1); }}><Icon name="step-back" /></button>
          <button className="cfg-icon-btn" type="button" aria-label={playing && !atEnd ? "Pause" : "Play"} onClick={toggle}><Icon name={playing && !atEnd ? "pause" : "play"} /></button>
          <button className="cfg-icon-btn" type="button" aria-label="Next step" aria-disabled={atEnd} onClick={() => { if (!atEnd) seekStep(step + 1); }}><Icon name="step-forward" /></button>
          <span className="cfg-transport__pos">{position}</span>
          <label className="cfg-field">Viewing speed<select className="field-input cfg-input" value={speed} onChange={event => setSpeed(Number(event.target.value))}>{SPEEDS.map(value => <option key={value} value={value}>{value}×</option>)}</select></label>
        </div>
        <p className="portal-context-note">{rollout.provenance.kind === "sample" ? "Sample rollout: the frame is a placeholder for the recorded simulator camera journal. " : "No camera journal is linked to this rollout; the frame is a placeholder. "}Playback speed is for viewing; this is not live video or a timing claim.{reduced ? " Reduced motion is on, so playback moves between key steps." : ""}</p>

        <section className="portal-panel ev-gap" aria-labelledby="ev-at-step-title">
          <div className="portal-panel-heading"><div><p className="portal-eyebrow">Step {fmtCount(step)} of {fmtCount(steps)} · {fmtFixed(atS)} s simulated</p><h2 id="ev-at-step-title">At this step</h2></div>{path && <PathChip path={path} />}</div>
          <dl className="portal-facts">
            <Fact label="Planner → skill" value={planned?.planner ?? NOT_REPORTED} detail={planned ? `Edge planner · ${fmtMs(planned.latencyMs)} · chosen at step ${fmtCount(planned.step)}` : rows.length ? "No planner decision before this step" : "No key steps stored for this rollout"} />
            <Fact label="Policy → action" value={current?.action ?? NOT_REPORTED} detail={current?.note ?? (current?.path ? `${PATH_LABEL[current.path]} path` : null)} />
            <Fact label="Latency" value={current ? (current.latencyMs !== null ? fmtMs(current.latencyMs) : current.path ? NOT_REPORTED : "Event") : NOT_REPORTED} detail={current ? LATENCY_NOTE[current.path ?? "none"] : null} />
            <Fact label="Last event" value={currentEvent >= 0 ? `${events[currentEvent].label} · ${fmtFixed(events[currentEvent].atS)} s` : "None yet"} detail={currentEvent >= 0 ? events[currentEvent].detail : null} />
            <Fact label="Safety" value={violations.length ? violations.map(item => `${item.checkId} at ${fmtFixed(item.atS)} s`).join(", ") : "No violations so far"} detail={violations.length ? violations.map(item => `${item.checkId} ${item.severity}`).join(", ") : null} />
          </dl>
        </section>
      </div>

      <div className="cfg-replay__side">
        <section className="portal-panel" aria-labelledby="ev-timeline-title">
          <div className="portal-panel-heading"><div><p className="portal-eyebrow">Simulated seconds · select to jump</p><h2 id="ev-timeline-title">Events and steps</h2></div><ProvenanceBadge provenance={rollout.provenance} now={now} /></div>
          {events.length ? <ol className="ev-events" aria-label="Episode events">
            {events.map((event, i) => <li key={`${event.atS}-${event.label}`} className={event.tone === "warning" ? "ev-event--warn" : event.tone === "good" ? "ev-event--good" : undefined}>
              <button className="ev-event" type="button" aria-current={i === currentEvent ? "true" : undefined} onClick={() => seek(event.atS)}>
                <span className="ev-event__t">{fmtFixed(event.atS)} s</span>
                <span><span className="ev-event__title">{event.label}</span>{event.detail && <span className="ev-event__detail">{event.detail}</span>}</span>
              </button>
            </li>)}
          </ol> : <p className="portal-empty">No events are stored for this rollout.</p>}
          <div className="cfg-subhead ev-gap"><h3>Steps around the playhead</h3>{rows.length > 0 && <span className="cfg-legend">Key steps {around.start + 1}–{around.start + around.rows.length} of {rows.length}</span>}</div>
          {rows.length ? <div className="portal-table-scroll" tabIndex={0} aria-label="Key steps, scroll horizontally">
            <table className="portal-table cfg-table">
              <caption>Planner decisions, chunk boundaries and routing changes · latency per decision or chunk</caption>
              <thead><tr><th scope="col" className="cfg-num">Step</th><th scope="col">Planner</th><th scope="col">Path</th><th scope="col">Policy action</th><th scope="col" className="cfg-num">Latency</th></tr></thead>
              <tbody>{around.rows.map(row => {
                const plan = row.planner ?? rows.filter(item => item.planner !== null && item.step <= row.step).at(-1)?.planner ?? null;
                return <tr key={row.step} className={row === current ? "cfg-tr--current" : undefined}>
                <th scope="row" className="cfg-num ev-step"><button className="portal-table-link" type="button" aria-label={`Go to step ${fmtCount(row.step)}`} onClick={() => seekStep(row.step)}>{fmtCount(row.step)}</button><small>{fmtFixed(row.atS)} s</small></th>
                <td>{plan ? <span className={row.planner ? undefined : "ev-muted"} title={row.planner ? plan : `Skill in effect: ${plan}`}>{plan.split("(")[0]}</span> : ""}</td>
                <td>{row.path ? <PathChip path={row.path} /> : ""}</td>
                <td className="cfg-wrap">{row.action}{row.note && <small>{row.note}</small>}</td>
                <td className="cfg-num">{row.latencyMs !== null ? fmtMs(row.latencyMs) : row.path ? NOT_REPORTED : "Event"}</td>
              </tr>;
              })}</tbody>
            </table>
          </div> : <p className="portal-empty">No key steps are stored for this rollout.</p>}
        </section>
        <Signals rollout={rollout} atS={atS} fallback={fallback} now={now} />
      </div>
    </div>
  </>;
}

/** Gripper aperture and end-effector speed on the simulated clock, with the playhead and fallback shading. */
function Signals({ rollout, atS, fallback, now }: { rollout: Rollout; atS: number; fallback: readonly PathSegment[]; now: number | null }) {
  const aperture = seriesWithin(rollout.signals?.gripperAperture, rollout.durationS);
  const speed = seriesWithin(rollout.signals?.eeSpeed, rollout.durationS);
  const limit = rollout.signals?.eeSpeedLimit ?? null;
  const speedValues = speed?.values.filter((value): value is number => value !== null) ?? [];
  const speedTop = Math.max(0.1, Math.ceil(Math.max(limit ?? 0, ...speedValues) * 10) / 10);
  return <section className="portal-panel" aria-labelledby="ev-signals-title">
    <div className="portal-panel-heading"><div><p className="portal-eyebrow">Synced with the playhead · simulated seconds</p><h2 id="ev-signals-title">Signals</h2></div><ProvenanceBadge provenance={rollout.provenance} now={now} /></div>
    {aperture || speed ? <>
      <div className="ev-plots">
        {aperture && <SignalPlot title="Gripper aperture · 0 closed, 1 open" values={aperture.values} spanS={aperture.spanS} domain={[0, 1]} ticks={[0, 0.5, 1]} atS={atS} shade={fallback}
          label={`Gripper aperture over ${fmtFixed(aperture.spanS)} simulated seconds, between 0 (closed) and 1 (open)`} />}
        {speed && <SignalPlot title="End-effector speed · m/s" values={speed.values} spanS={speed.spanS} domain={[0, speedTop]} ticks={[0, speedTop / 2, speedTop].map(value => Math.round(value * 100) / 100)} atS={atS} shade={fallback}
          reference={limit} referenceLabel={limit !== null ? `Limit ${fmtFixed(limit, 2)}` : undefined}
          label={`End-effector speed over ${fmtFixed(speed.spanS)} simulated seconds, peak ${fmtUnit(Math.max(...speedValues), "m/s", 2)}${limit !== null ? `, limit ${fmtUnit(limit, "m/s", 2)}` : ""}`} />}
      </div>
      <div className="cfg-legend cfg-plot__legend">
        <span><LegendKey kind="line" />Signal</span>
        {fallback.length > 0 && <span><i className="ev-key-shade" aria-hidden="true" />Fallback {fallback.map(segment => `${fmtFixed(segment.from)}–${fmtFixed(segment.to)} s`).join(", ")}</span>}
        {limit !== null && <span><LegendKey kind="ref" />Speed limit {fmtUnit(limit, "m/s", 2)}</span>}
        <span><i className="ev-key-cursor" aria-hidden="true" />Playhead</span>
      </div>
    </> : <p className="portal-empty">No signals are stored for this rollout.</p>}
  </section>;
}

function SignalPlot({ title, values, spanS, domain, ticks, atS, shade, reference, referenceLabel, label }: {
  title: string; values: ReadonlyArray<number | null>; spanS: number; domain: readonly [number, number]; ticks: readonly number[]; atS: number;
  shade: readonly PathSegment[]; reference?: number | null; referenceLabel?: string; label: string;
}) {
  const geometry = plotGeometry(values.map(value => ({ p50: value })), domain, ticks, reference);
  const x = (seconds: number) => Math.round(Math.min(Math.max(seconds / spanS, 0), 1) * 10000) / 100;
  const xLabels = [0, 0.25, 0.5, 0.75, 1].map((part, i) => i === 0 ? "0 s" : i === 4 ? `${fmtFixed(spanS)} s` : fmtFixed(spanS * part));
  return <figure className="cfg-plot cfg-plot--sm">
    <figcaption className="cfg-plot__head"><span className="cfg-plot__title">{title}</span></figcaption>
    <div className="cfg-plot__body">
      <div className="cfg-plot__y" aria-hidden="true">{geometry.ticks.map(tick => <span key={tick.label} style={{ top: tick.top }}>{tick.label}</span>)}</div>
      <div className="cfg-plot__frame">
        <svg className="cfg-plot__svg" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label={label}>
          {shade.map(segment => <path key={segment.from} className="ev-shade" d={`M${x(segment.from)} 0H${x(segment.to)}V100H${x(segment.from)}Z`} />)}
          <path className="cfg-plot__grid" d={geometry.grid} />
          {geometry.ref && <path className="cfg-plot__ref" d={geometry.ref} />}
          <polyline className="cfg-plot__line" points={geometry.line} />
        </svg>
        {geometry.refTop && referenceLabel && <span className="cfg-plot__ref-label" style={{ top: geometry.refTop }}>{referenceLabel}</span>}
        <span className="cfg-plot__cursor" style={{ left: `${x(atS)}%` }} />
      </div>
    </div>
    <div className="cfg-plot__x" aria-hidden="true">{xLabels.map((text, i) => <span key={i} style={{ left: `${i * 25}%` }}>{text}</span>)}</div>
  </figure>;
}

/* ---------- recorded episode ---------- */

interface RecordingMeta { episode_id: string; mission_id: string; release_digest: string; steps: number; skill: string | null; planner_ms: number | null; wall_seconds: number | null; sim_seconds: number | null; source: string }

/** A published episode: facts from its recording and the existing replay player with the real camera frames. */
function RecordedReplay({ target, suite, now }: { target: Extract<ReplayTarget, { kind: "recorded" }>; suite: EvalSuite | null; now: number | null }) {
  const valid = ID_PATTERN.test(target.episodeId);
  const { state } = usePlatformRead<RecordingMeta>(valid ? `episodes/${target.episodeId}/replay` : null);
  const meta = state.status === "ready" ? state.data : null;
  const pending = state.status === "loading" ? "Reading…" : null;
  const value = (text: string | null): ReactNode => text ?? pending ?? <NotReported />;
  const slices = target.rollout?.sliceIds.map(id => sliceInfo(suite, id).name) ?? [];
  return <>
    <dl className="portal-health-strip ev-strip" aria-label="Episode facts">
      <Fact label="Outcome" value={target.outcome ? <OutcomeBadge outcome={target.outcome} /> : <NotReported />} detail={target.rollout?.failureMode ?? null} />
      <Fact label="Recorded actions" value={value(meta ? fmtCount(meta.steps) : null)} detail="One camera frame per action; frame 0 is the initial observation" />
      <Fact label="Simulated time" value={value(meta?.sim_seconds != null ? fmtUnit(meta.sim_seconds, "s", 3) : null)} detail="Simulated clock" />
      <Fact label="Wall-clock time" value={value(meta?.wall_seconds != null ? fmtDuration(meta.wall_seconds) : null)} detail="Lockstep: physics waited for inference; not a timing claim" />
      <Fact label="Planner → skill" value={value(meta ? meta.skill ?? "No planner decision recorded" : null)} detail={meta?.planner_ms != null ? `Planner ${fmtMs(meta.planner_ms, 1)}` : null} />
      <Fact label="Evidence" value={<ProvenanceBadge provenance={target.provenance} now={now} />} detail={[meta?.source, meta ? `release ${meta.release_digest.slice(0, 12)}` : null, slices.length ? `slice ${slices.join(", ")}` : null].filter(Boolean).join(" · ") || provenanceDetail(target.provenance)} />
    </dl>
    {valid ? <section className="portal-panel ev-recorded" aria-label="Recorded camera journal">
      <div className="console-shell"><EpisodeReplay key={target.episodeId} episodeId={target.episodeId} /></div>
    </section> : <p className="portal-empty">This episode id cannot be replayed.</p>}
  </>;
}
