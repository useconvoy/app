"use client";

import Link from "next/link";
import type { MouseEvent, ReactNode } from "react";
import { runHref, sliceInfo, successShare, taskName } from "@/lib/configurations/client";
import {
  clockLabel, clockName, countNoun, familyOf, familyResults, failedEpisodes, fmtDuration, gateRows, largestSliceGap, pointsDelta, rowInterval, rowShare, runShare,
  safetyRows, secondsBetween, signed, successInterval, valueDelta,
} from "@/lib/configurations/eval-run";
import { fmtCi, fmtCount, fmtDateTime, fmtFixed, fmtMs, fmtRatio, fmtSeconds, fmtShare, NOT_REPORTED, provenanceDetail, runLabel } from "@/lib/configurations/format";
import type { Configuration, ConvoyWorkspace, EvalRun, EvalSuite, Robot, Rollout, StoredProvenance } from "@/lib/configurations/types";
import type { EvaluationRun } from "@/lib/platform/client";
import { Badge, NotReported, OutcomeBadge, ProvenanceBadge } from "../../Badges";
import { Legend } from "../../Charts";
import { DataTable, type Column } from "../../DataTable";
import { FactList } from "../../Facts";
import { FailureBars } from "../../FailureBars";
import { Icon } from "../../Icons";
import { Delta, KpiGrid, KpiTile } from "../../Kpi";
import { Notice } from "../../Notice";
import { ProgressBar } from "../../ProgressBar";
import { Meter, SliceTable } from "../../Slices";
import { EmptyState } from "../../States";
import type { Remote } from "./hooks";

const pct = (share: number) => `${Math.round(share * 1000) / 10}%`;
const live = (run: EvalRun) => run.status === "running" || run.status === "queued";
const plural = (count: number, one: string, many = `${one}s`) => `${fmtCount(count)} ${count === 1 ? one : many}`;

/** "↑ 2.8 pts vs r3 (75.3 %)": arrow for sighted readers, words for screen readers; tone by which direction is better. */
function Change({ delta, better, unit, against }: { delta: number | null; better: "higher" | "lower"; unit: string; against: string }) {
  if (delta === null) return null;
  if (delta === 0) return <Delta>Same as {against}</Delta>;
  const good = better === "higher" ? delta > 0 : delta < 0;
  return <Delta tone={good ? "good" : "bad"}><span aria-hidden="true">{delta > 0 ? "↑" : "↓"}</span><span className="cfg-sr">{delta > 0 ? "Up" : "Down"}</span> {fmtFixed(Math.abs(delta))}{unit} vs {against}</Delta>;
}

/* ---------- progress ---------- */

/** Running: progress with a partial-results note. Queued: what happens next, honestly. */
export function RunProgress({ run, now }: { run: EvalRun; now: number | null }) {
  const progress = run.progress;
  if (run.status === "running" && progress) {
    const share = progress.total ? progress.done / progress.total : 0;
    const elapsed = run.startedAt && now !== null ? fmtDuration((now - Date.parse(run.startedAt)) / 1000) : null;
    return <div className="ev-progress">
      <Notice tone="info" icon="clock">
        <strong>{runLabel(run)} is in progress:</strong> {fmtCount(progress.done)} of {plural(progress.total, "episode")} ({fmtShare(share)}){elapsed ? `, running for ${elapsed}` : ""}. The metrics below cover the finished episodes and will change; the gate is decided when the run finishes.
        <ProgressBar wide done={progress.done} total={progress.total} label={`${runLabel(run)} episodes complete`} />
      </Notice>
    </div>;
  }
  if (run.status === "queued") {
    const fromWorkspace = run.provenance.kind === "not-reported";
    return <div className="ev-progress">
      <Notice tone="info" icon="clock">
        <strong>{runLabel(run)} is queued</strong>{progress ? ` · ${fmtCount(progress.done)} of ${plural(progress.total, "episode")}` : ""}.{" "}
        {fromWorkspace
          ? "It was queued from this workspace. No evaluation runner is connected, so it stays queued until a runner reports results."
          : run.note ? `${run.note}.` : "Results appear here as episodes finish."}
      </Notice>
    </div>;
  }
  if (run.status === "failed" || run.status === "cancelled") {
    return <Notice tone="warning">{runLabel(run)} {run.status === "failed" ? "failed" : "was cancelled"} after {plural(run.counts.episodes, "episode")}. The results below cover only those episodes.</Notice>;
  }
  return null;
}

/* ---------- metric tiles ---------- */

/** Safety violations per check as small columns (the counts are also in the label). */
function SafetyColumns({ suite, run }: { suite: EvalSuite; run: EvalRun }) {
  const rows = safetyRows(suite, run.safety);
  if (!rows.length) return null;
  const max = Math.max(1, ...rows.map(row => row.count));
  return <div className="ev-sbars" role="img" aria-label={`Safety violations by check over ${plural(run.counts.episodes, "episode")}: ${rows.map(row => `${row.id} ${row.count}`).join(", ")}`}>
    {rows.map(row => <span key={row.id} className={`ev-sbar${row.count ? "" : " ev-sbar--zero"}`}>
      <span className="ev-sbar__n">{row.count}</span>
      <span className="ev-sbar__col"><span className="ev-sbar__fill" style={{ height: pct(row.count / max) }} /></span>
      <span>{row.id}</span>
    </span>)}
  </div>;
}

/**
 * Success with n and 95 % CI, safety per 100 with the per-check columns, median
 * and p90 episode time on its clock, and edge latency recorded on bench hardware
 * (this run's, or the robot's newest latency soak, labelled as such).
 */
export function RunMetrics({ workspace, run, suite, robot, configuration, baseline, hilRun, gateShare, now }: {
  workspace: ConvoyWorkspace; run: EvalRun; suite: EvalSuite | null; robot: Robot; configuration: Configuration | null; baseline: EvalRun | null;
  hilRun: EvalRun | null; gateShare: number | null; now: number | null;
}) {
  const share = successShare(run);
  const { ci, computed } = successInterval(run);
  const episodes = run.counts.episodes;
  const partial = run.status === "running";
  const against = (value: string) => baseline ? `${baseline.rev} (${value})` : "";
  const baseShare = runShare(baseline);
  const time = run.episodeTime;
  const hil = hilRun?.hilLatency ?? null;
  const hilHref = hilRun && hilRun.id !== run.id ? runHref(workspace, hilRun) : null;
  const revision = configuration?.revisions.find(item => item.rev === run.rev) ?? null;
  const partialBadge = partial && run.progress ? <Badge tone="info">Partial · {fmtCount(run.progress.done)}/{fmtCount(run.progress.total)}</Badge> : null;
  const noun = countNoun(run);
  const count = (value: number) => `${fmtCount(value)} ${value === 1 ? noun.one : noun.many}`;
  // Safety and episode time belong to every suite run; other kinds show them only when they report them.
  const showSafety = run.kind === "suite" || run.safety !== null;
  const showTime = run.kind === "suite" || time !== null;
  const tiles = 1 + Number(showSafety) + Number(showTime) + Number(!!hil);
  return <>
    <KpiGrid columns={tiles >= 4 ? 4 : 3} className="ev-kpis">
      <KpiTile label={run.kind === "hil-latency" ? "Scored correct" : "Success rate"} value={share === null ? null : share * 100} unit="%" provenance={run.provenance} now={now}
        sub={share === null
          ? (episodes ? "Not scored in this run" : `No ${noun.many} finished yet`)
          : `${fmtCount(run.counts.successes)} of ${count(episodes)}${partial ? " so far" : ""}${gateShare !== null ? ` · gate ${fmtShare(gateShare, 0)}` : ""}`}
        foot={<>{partialBadge}<Change delta={baseline ? pointsDelta(share, baseShare) : null} better="higher" unit=" pts" against={against(fmtShare(baseShare))} /></>}>
        {share !== null && <p className="cfg-kpi__sub">95 % {computed ? "Wilson " : ""}CI {fmtCi(ci)}</p>}
      </KpiTile>
      {showSafety && <KpiTile label="Safety violations" value={run.safety?.per100 ?? null} unit="per 100 episodes" provenance={run.provenance} now={now}
        sub={run.safety
          ? `${plural(run.safety.episodesWithViolations, "episode")} with a violation · ${run.safety.critical} critical · ${run.safety.major} major · ${run.safety.minor} minor`
          : "Safety is not reported for this run"}
        foot={<>{run.safety && partialBadge}<Change delta={baseline?.safety ? valueDelta(run.safety?.per100, baseline.safety.per100) : null} better="lower" unit="" against={against(fmtFixed(baseline?.safety?.per100))} /></>}>
        {run.safety && suite && <SafetyColumns suite={suite} run={run} />}
      </KpiTile>}
      {showTime && <KpiTile label="Median episode time" value={time?.medianS ?? null} unit={time ? `s ${clockLabel(time.clock)}` : undefined} provenance={run.provenance} now={now}
        sub={time ? `p90 ${fmtSeconds(time.p90S)} · n = ${count(episodes)}${partial ? " so far" : ""}` : "Episode time is not reported for this run"}
        foot={<>{time && partialBadge}<Change delta={baseline?.episodeTime && time && baseline.episodeTime.clock === time.clock ? valueDelta(time.medianS, baseline.episodeTime.medianS) : null} better="lower" unit=" s" against={against(fmtSeconds(baseline?.episodeTime?.medianS))} /></>} />}
      {hil && hilRun && <KpiTile label={`Edge planner latency · ${robot.name}`} series="edge" value={hil.plannerMs.p50} digits={0} unit="ms p50" provenance={hil.provenance} now={now}
        sub={[`p95 ${fmtMs(hil.plannerMs.p95)}`, hil.ttftMs ? `TTFT p50 ${fmtMs(hil.ttftMs.p50)}` : null, hil.plannerMs.n ? `n = ${plural(hil.plannerMs.n, "request")}` : null].filter(Boolean).join(" · ")}
        foot={hilHref ? <Link className="cfg-btn-text ev-tile-link" href={hilHref}>{runLabel(hilRun)} · {hilRun.title}</Link> : null}>
        {hilHref && <p className="cfg-kpi__sub">Recorded on bench hardware in {runLabel(hilRun)}, not during this run</p>}
        {!!revision?.cloudModels.length && <p className="cfg-kpi__sub">Cloud chunk round trip: <NotReported /></p>}
      </KpiTile>}
    </KpiGrid>
    {suite && <p className="portal-context-note">{suite.timing}.{run.simEngine ? ` Simulation: ${run.simEngine}.` : ""}</p>}
  </>;
}

/* ---------- gate ---------- */

/** "Overall success ≥ 70 %", or "Critical safety events: none" when the target is not a comparison. */
const criterionText = (label: string, target: string | null) => !target ? label
  : /^[≥≤<>=]/.test(target.trim()) ? `${label} ${target}` : `${label}: ${target.charAt(0).toLowerCase()}${target.slice(1)}`;

export function GatePanel({ suite, run, baseline, configuration, now }: { suite: EvalSuite | null; run: EvalRun; baseline: EvalRun | null; configuration: Configuration | null; now: number | null }) {
  const rows = gateRows(suite, run);
  if (!rows.length) return null;
  const passed = rows.filter(row => row.state === "pass").length;
  const gate = run.gate;
  const title = gate ? (gate.passed ? "Promotion gate passed" : "Below the promotion gate") : live(run) ? "Gate decided when the run finishes" : "No gate decision recorded";
  const candidate = configuration?.candidateRev === run.rev ? " candidate" : "";
  const versus = baseline ? ` vs ${baseline.rev === configuration?.productionRev ? "production " : ""}${baseline.rev} (${runLabel(baseline)})` : "";
  return <section className="cfg-section portal-panel" aria-labelledby="ev-gate-title">
    <div className="portal-panel-heading">
      <div><p className="portal-eyebrow">{run.rev}{candidate}{versus} · {plural(rows.length, "criterion", "criteria")}</p><h2 id="ev-gate-title">{title}</h2></div>
      <span className="cfg-badges">{gate && <Badge tone={gate.passed ? "success" : "warning"}>{passed} of {rows.length} pass</Badge>}<ProvenanceBadge provenance={run.provenance} now={now} /></span>
    </div>
    <ul className="cfg-checks ev-gate" aria-label="Gate criteria">
      {rows.map(row => {
        const state = row.state === "pass" ? "pass" : row.state === "fail" ? "warn" : "pending";
        return <li key={row.id} className={`cfg-check cfg-check--${state}`}>
          <span className="cfg-check__icon"><Icon name={row.state === "pass" ? "check" : row.state === "fail" ? "warning" : "clock"} /></span>
          <div><p className="cfg-check__title">{criterionText(row.label, row.target)}</p><p className="cfg-check__detail">{row.actual ?? (gate ? "No result recorded" : "Decided when the run finishes")}</p></div>
          <div className="cfg-check__meta"><span className="cfg-check__verdict">{row.state === "pass" ? "Pass" : row.state === "fail" ? "Below gate" : "Pending"}</span></div>
        </li>;
      })}
    </ul>
  </section>;
}

/* ---------- comparison ---------- */

/** Side-by-side figures for this run and the production-revision run, and the slices that moved most. */
export function ComparePanel({ run, baseline, suite, configuration, headingRef }: { run: EvalRun; baseline: EvalRun; suite: EvalSuite | null; configuration: Configuration | null; headingRef: (node: HTMLHeadingElement | null) => void }) {
  const mine = successShare(run), theirs = successShare(baseline);
  const gateText = (item: EvalRun) => item.gate ? `${item.gate.passed ? "Passed" : "Below gate"} · ${item.gate.results.filter(result => result.passed).length} of ${item.gate.results.length}` : live(item) ? "Not decided yet" : NOT_REPORTED;
  const safety = (item: EvalRun) => item.safety ? `${item.safety.critical} · ${item.safety.major} · ${item.safety.minor}` : NOT_REPORTED;
  // Times from different clocks are never subtracted.
  const sameClock = !!run.episodeTime && run.episodeTime.clock === baseline.episodeTime?.clock;
  const rows: Array<{ label: string; mine: string; theirs: string; change: string }> = [
    { label: "Success rate", mine: `${fmtShare(mine)} · ${fmtRatio(run.counts.successes, run.counts.episodes)}`, theirs: `${fmtShare(theirs)} · ${fmtRatio(baseline.counts.successes, baseline.counts.episodes)}`, change: changeText(pointsDelta(mine, theirs), " points") },
    { label: "95 % CI", mine: fmtCi(successInterval(run).ci), theirs: fmtCi(successInterval(baseline).ci), change: "" },
    { label: "Safety violations per 100", mine: fmtFixed(run.safety?.per100), theirs: fmtFixed(baseline.safety?.per100), change: changeText(valueDelta(run.safety?.per100, baseline.safety?.per100), "") },
    { label: "Critical · major · minor", mine: safety(run), theirs: safety(baseline), change: "" },
    { label: "Median episode time", mine: fmtSeconds(run.episodeTime?.medianS), theirs: fmtSeconds(baseline.episodeTime?.medianS), change: sameClock ? changeText(valueDelta(run.episodeTime?.medianS, baseline.episodeTime?.medianS), " s") : "" },
    { label: "p90 episode time", mine: fmtSeconds(run.episodeTime?.p90S), theirs: fmtSeconds(baseline.episodeTime?.p90S), change: sameClock ? changeText(valueDelta(run.episodeTime?.p90S, baseline.episodeTime?.p90S), " s") : "" },
    { label: "Gate", mine: gateText(run), theirs: gateText(baseline), change: "" },
  ];
  const other = new Map(baseline.slices.map(result => [result.sliceId, result]));
  const moved = run.slices.flatMap(result => {
    const match = other.get(result.sliceId);
    const delta = match ? pointsDelta(rowShare(result), rowShare(match)) : null;
    return match && delta !== null ? [{ result, match, delta }] : [];
  }).toSorted((a, b) => Math.abs(b.delta) - Math.abs(a.delta)).slice(0, 3);
  const production = baseline.rev === configuration?.productionRev ? ", production" : "";
  return <section className="cfg-section portal-panel" id="compare" aria-labelledby="ev-compare-title">
    <div className="portal-panel-heading">
      <div><p className="portal-eyebrow">Same suite{suite ? ` · ${suite.name} ${suite.version}` : ""}{live(run) ? " · this run is still in progress" : ""}</p>
        <h2 id="ev-compare-title" ref={headingRef} tabIndex={-1}>{runLabel(run)} ({run.rev}) compared with {runLabel(baseline)} ({baseline.rev}{production})</h2></div>
    </div>
    <div className="portal-table-scroll" tabIndex={0} aria-label="Run comparison, scroll horizontally">
      <table className="portal-table cfg-table ev-compare">
        <caption>Figures as stored for each run · change = this run minus {runLabel(baseline)}</caption>
        <thead><tr><th scope="col">Metric</th>
          <th scope="col" className="cfg-num">{runLabel(run)} · {run.rev} <ProvenanceBadge provenance={run.provenance} /></th>
          <th scope="col" className="cfg-num">{runLabel(baseline)} · {baseline.rev} <ProvenanceBadge provenance={baseline.provenance} /></th>
          <th scope="col" className="cfg-num">Change</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.label}><th scope="row">{row.label}</th><td className="cfg-num">{row.mine}</td><td className="cfg-num">{row.theirs}</td><td className="cfg-num">{row.change}</td></tr>)}</tbody>
      </table>
    </div>
    {moved.length > 0 && <>
      <div className="cfg-subhead ev-gap"><h3>Largest changes by slice</h3><span className="cfg-legend">Success, this run against {runLabel(baseline)}</span></div>
      <ul className="ev-moves">
        {moved.map(({ result, match, delta }) => {
          const info = sliceInfo(suite, result.sliceId);
          return <li key={result.sliceId}><span>{info.name}<small>{info.family}</small></span>
            <span className="ev-moves__val">{fmtShare(rowShare(match))} → {fmtShare(rowShare(result))}<small>n = {fmtCount(match.episodes)} and {fmtCount(result.episodes)}</small></span>
            <Delta tone={delta > 0 ? "good" : delta < 0 ? "bad" : undefined}>{signed(delta)} points</Delta></li>;
        })}
      </ul>
    </>}
  </section>;
}
const changeText = (delta: number | null, unit: string) => delta === null ? NOT_REPORTED : `${signed(delta)}${unit}`;

/* ---------- per-task results ---------- */

interface TaskRow { id: string; name: string; reference: number | null; episodes: number; successes: number; share: number | null; ci: [number, number] | null; safety: number | null; medianS: number | null; base: { share: number | null } | null; delta: number | null }

export function TaskResults({ run, suite, baseline, now }: { run: EvalRun; suite: EvalSuite | null; baseline: EvalRun | null; now: number | null }) {
  const baseTasks = new Map((baseline?.perTask ?? []).map(task => [task.taskId, task]));
  const rows: TaskRow[] = run.perTask.map(task => {
    const base = baseTasks.get(task.taskId);
    const share = rowShare(task), baseShare = base ? rowShare(base) : null;
    return {
      id: task.taskId, name: taskName(suite, task.taskId), reference: suite?.tasks.find(item => item.id === task.taskId)?.referenceMedianS ?? null,
      episodes: task.episodes, successes: task.successes, share, ci: rowInterval(task), safety: task.safetyPer100, medianS: task.medianS,
      base: base ? { share: baseShare } : null, delta: base ? pointsDelta(share, baseShare) : null,
    };
  });
  const columns: Array<Column<TaskRow>> = [
    { key: "task", header: "Task", cell: row => row.name, detail: row => row.reference !== null ? `Reference median ${fmtSeconds(row.reference)}` : null },
    { key: "meter", header: "Success", cell: row => row.share === null ? null : <Meter rate={row.share} ci={row.ci} /> },
    { key: "rate", header: "Rate", numeric: true, sort: row => row.share, firstDir: "asc", cell: row => fmtShare(row.share), detail: row => fmtRatio(row.successes, row.episodes) },
    { key: "ci", header: "95 % CI", numeric: true, cell: row => fmtCi(row.ci, 0) },
    { key: "safety", header: "Safety / 100", numeric: true, cell: row => row.safety === null ? NOT_REPORTED : fmtFixed(row.safety) },
    { key: "median", header: "Median time", numeric: true, cell: row => fmtSeconds(row.medianS) },
    ...(baseline ? [{
      key: "delta", header: `vs ${baseline.rev}`, numeric: true, sort: (row: TaskRow) => row.delta, firstDir: "asc" as const,
      cell: (row: TaskRow) => row.delta === null ? NOT_REPORTED : <Delta tone={row.delta >= 1 ? "good" : row.delta <= -1 ? "bad" : undefined}>{signed(row.delta)}</Delta>,
      detail: (row: TaskRow) => row.base ? `${fmtShare(row.base.share)} → ${fmtShare(row.share)}` : null,
    }] : []),
  ];
  const perTask = rows.length ? Math.round(rows.reduce((sum, row) => sum + row.episodes, 0) / rows.length) : 0;
  return <section className="cfg-section portal-panel" aria-labelledby="ev-tasks-title">
    <div className="portal-panel-heading">
      <div><p className="portal-eyebrow">{plural(rows.length, "task")} · about {plural(perTask, "episode")} per task{live(run) ? " so far" : ""}</p><h2 id="ev-tasks-title">Results by task</h2></div>
      <ProvenanceBadge provenance={run.provenance} now={now} />
    </div>
    {rows.length ? <DataTable label="Results by task" rows={rows} rowKey={row => row.id} columns={columns}
      caption={`Success per task with 95 % Wilson CI · median time on the ${run.episodeTime ? clockName(run.episodeTime.clock) : "run’s clock"}${baseline ? ` · change vs ${baseline.rev} (${runLabel(baseline)}) in points` : ""}`} />
      : <p className="portal-empty">No per-task results are recorded for this run yet.</p>}
  </section>;
}

/* ---------- scenario slices ---------- */

export function SlicesSection({ workspace, run, suite, other, gateShare, sliceFilter, filterHref, onToggleSlice, replayFor, onOpenReplay, now }: {
  workspace: ConvoyWorkspace; run: EvalRun; suite: EvalSuite; other: EvalRun | null; gateShare: number | null; sliceFilter: string | null;
  /** Links the weakest slices to the filtered rollouts table; leave out when the run stores no rollouts. */
  filterHref?: (sliceId: string) => string; onToggleSlice: (sliceId: string) => void; replayFor: (rollout: Rollout) => string;
  onOpenReplay: (event: MouseEvent<HTMLAnchorElement>) => void; now: number | null;
}) {
  const families = suite.sliceFamilies.filter(family => family.slices.some(slice => run.slices.some(result => result.sliceId === slice.id)));
  const gap = other ? largestSliceGap(run.slices, other.slices) : null;
  const showCallout = !!(gap && other && Math.abs(gap.points) >= 10);
  return <section className="cfg-section" aria-labelledby="ev-slices-title">
    <div className="portal-section-label"><h2 id="ev-slices-title">Scenario slices</h2>
      <span>Where {run.rev} is weakest · {plural(families.length, "slice family", "slice families")}, {plural(run.slices.length, "slice")}, {plural(run.slices.reduce((sum, slice) => sum + slice.episodes, 0), "episode")}</span></div>
    {showCallout && gap && other && <SliceCallout workspace={workspace} run={run} other={other} suite={suite} sliceId={gap.sliceId} gateShare={gateShare} active={sliceFilter === gap.sliceId}
      onToggle={() => onToggleSlice(gap.sliceId)} replayFor={replayFor} onOpenReplay={onOpenReplay} now={now} />}
    <div className="portal-panel">
      <div className="cfg-subhead"><h3>Success by slice · grouped by family, the two weakest flagged</h3>
        <div className="cfg-inline"><Legend items={[...(gateShare !== null ? [{ key: "ref" as const, label: `Slice-family gate ${fmtShare(gateShare, 0)}` }] : []), { label: "Success, whisker = 95 % CI" }, { series: "flag", label: "Two weakest slices" }]} /><ProvenanceBadge provenance={run.provenance} now={now} /></div>
      </div>
      <SliceTable suite={suite} results={run.slices} gate={gateShare} filterHref={filterHref}
        caption={`${runLabel(run)} · ${run.rev} · success per slice with 95 % Wilson CI${gateShare !== null ? ` · dashed line = slice-family gate ${fmtShare(gateShare, 0)}` : ""} · median time on the ${run.episodeTime ? clockName(run.episodeTime.clock) : "run’s clock"} · n = episodes${live(run) ? " so far" : ""}`} />
    </div>
  </section>;
}

/** The slice where this run and another configuration's run of the same suite differ most. */
function SliceCallout({ workspace, run, other, suite, sliceId, gateShare, active, onToggle, replayFor, onOpenReplay, now }: {
  workspace: ConvoyWorkspace; run: EvalRun; other: EvalRun; suite: EvalSuite; sliceId: string; gateShare: number | null; active: boolean; onToggle: () => void;
  replayFor: (rollout: Rollout) => string; onOpenReplay: (event: MouseEvent<HTMLAnchorElement>) => void; now: number | null;
}) {
  const info = sliceInfo(suite, sliceId);
  const description = suite.sliceFamilies.flatMap(family => family.slices).find(slice => slice.id === sliceId)?.description;
  const family = familyOf(suite, sliceId);
  const mineFamily = familyResults(suite, run.slices).find(item => item.id === family?.id) ?? null;
  const theirFamily = familyResults(suite, other.slices).find(item => item.id === family?.id) ?? null;
  const mine = run.slices.find(result => result.sliceId === sliceId)!, theirs = other.slices.find(result => result.sliceId === sliceId)!;
  const configName = (configId: string) => workspace.configurations.find(config => config.id === configId)?.name ?? configId;
  const below = (share: number | null) => gateShare !== null && share !== null && share < gateShare;
  const inSlice = workspace.rollouts.filter(rollout => rollout.runId === run.id && rollout.sliceIds.includes(sliceId));
  const replay = inSlice.toSorted((a, b) => Number(!!b.stepDetail) - Number(!!a.stepDetail) || Number(b.outcome === "succeeded") - Number(a.outcome === "succeeded"))[0] ?? null;
  const row = (item: typeof mine, label: string, note: string, flagged: boolean) => {
    const ci = rowInterval(item);
    return <div className={`ev-cmp__row${flagged ? " cfg-series--flag" : ""}`}><span>{label}<small>{note}</small></span>
      <Meter rate={rowShare(item) ?? 0} ci={ci} gate={gateShare} />
      <span className="ev-cmp__val">{fmtShare(rowShare(item))}<small>{fmtRatio(item.successes, item.episodes)} · CI {fmtCi(ci, 0)}</small></span></div>;
  };
  const sameN = mine.episodes === theirs.episodes;
  return <div className="portal-panel ev-callout">
    <div>
      <p className="portal-eyebrow">{family ? `${family.name} · ` : ""}{info.name} · n = {sameN ? `${fmtCount(mine.episodes)} per configuration` : `${fmtCount(mine.episodes)} and ${fmtCount(theirs.episodes)}`}</p>
      <h3 className="ev-callout__title">{info.name}: {fmtShare(rowShare(mine))} on {configName(run.configId)} {run.rev}, {fmtShare(rowShare(theirs))} on {configName(other.configId)} {other.rev}.</h3>
      {description && <p className="ev-callout__text">{description}.</p>}
      {family && mineFamily && theirFamily && <p className="ev-callout__text">{family.name} family: {fmtShare(mineFamily.share)} in {runLabel(run)} and {fmtShare(theirFamily.share)} in {runLabel(other)}{gateShare !== null ? `, against the ${fmtShare(gateShare, 0)} slice-family gate` : ""}.</p>}
    </div>
    <div>
      <div className="ev-cmp" role="img" aria-label={`${info.name} success: ${runLabel(run)} ${fmtShare(rowShare(mine))}, ${fmtRatio(mine.successes, mine.episodes)}; ${runLabel(other)} ${fmtShare(rowShare(theirs))}, ${fmtRatio(theirs.successes, theirs.episodes)}${gateShare !== null ? `; slice-family gate ${fmtShare(gateShare, 0)}` : ""}`}>
        {row(mine, `${configName(run.configId)} ${run.rev}`, `${runLabel(run)} · this run`, below(mineFamily?.share ?? null))}
        {row(theirs, `${configName(other.configId)} ${other.rev}`, `${runLabel(other)}${other.status === "below-gate" ? " · below gate" : ""}`, below(theirFamily?.share ?? null))}
      </div>
      <Legend items={[...(gateShare !== null ? [{ key: "ref" as const, label: `Slice-family gate ${fmtShare(gateShare, 0)}` }] : []), { label: "Success, whisker = 95 % CI" }, { series: "flag", label: "Family below gate" }]} />
      <div className="cfg-inline ev-callout__actions">
        {inSlice.length > 0 && <button className="btn btn-secondary cfg-btn ev-toggle" type="button" aria-pressed={active} onClick={onToggle}>Filter rollouts</button>}
        {replay && <Link className="cfg-btn-text" href={replayFor(replay)} data-replay-link={replay.id} onClick={onOpenReplay}>Replay {replay.id} ▸</Link>}
        <ProvenanceBadge provenance={run.provenance} now={now} />
      </div>
    </div>
  </div>;
}

/* ---------- failures and safety ---------- */

export function FailurePanels({ run, suite, now }: { run: EvalRun; suite: EvalSuite | null; now: number | null }) {
  const failed = failedEpisodes(run);
  const episodes = run.counts.episodes;
  const rows = suite ? safetyRows(suite, run.safety) : [];
  const events = run.safety ? run.safety.critical + run.safety.major + run.safety.minor : null;
  const maxSafety = Math.max(1, ...rows.map(row => row.count));
  const critical = (suite?.safety ?? []).filter(check => check.severity === "critical" || check.criticalWhen);
  const showSafety = !!suite && rows.length > 0;
  if (!run.failureModes.length && !showSafety) return null;
  return <section className={`cfg-section${showSafety ? " portal-two-column" : ""}`} aria-label="Failure modes and safety events">
    <div className="portal-panel">
      <div className="portal-panel-heading">
        <div><p className="portal-eyebrow">{failed === null ? "Not scored" : run.kind === "hil-latency" ? `${fmtCount(failed)} of ${fmtCount(episodes)} requests scored incorrect` : `${plural(failed, "failed episode")} of ${fmtCount(episodes)}${live(run) ? " so far" : ""}`} · one primary mode each</p><h2>Failure modes</h2></div>
        <ProvenanceBadge provenance={run.provenance} now={now} />
      </div>
      <FailureBars items={run.failureModes} total={failed ?? 0} label={`Failure modes${failed !== null ? `, ${fmtCount(failed)} of ${fmtCount(episodes)} ${run.kind === "hil-latency" ? "requests scored incorrect" : "episodes failed"}` : ""}`} />
    </div>
    {showSafety && run.safety && <div className="portal-panel">
      <div className="portal-panel-heading">
        <div><p className="portal-eyebrow">{plural(events ?? 0, "event")} in {plural(episodes, "episode")} · {fmtFixed(run.safety.per100)} per 100</p><h2>Safety events by type</h2></div>
        <ProvenanceBadge provenance={run.provenance} now={now} />
      </div>
      <ul className="cfg-hbars" aria-label={`Safety events by check, ${plural(events ?? 0, "event")} in ${plural(episodes, "episode")}`}>
        {rows.map(row => <li className="cfg-hbar" key={row.id}><span>{row.id} {row.name}</span>
          <span className="cfg-hbar__track" aria-hidden="true"><span className="cfg-hbar__fill" style={{ width: pct(row.count / maxSafety) }} /></span>
          <span className="cfg-hbar__value">{row.count}{row.severity ? ` · ${row.severity}` : ""}</span></li>)}
      </ul>
      <p className="portal-context-note">Scored in simulation against the suite’s {plural(suite?.safety.length ?? 0, "safety check")}.{critical.length ? ` Critical: ${critical.map(check => check.criticalWhen ? `${check.id} ${check.criticalWhen.replace(/^Critical /, "").toLowerCase()}` : `${check.id} ${check.name.toLowerCase()}`).join("; ")}.` : ""}</p>
    </div>}
    {showSafety && !run.safety && <div className="portal-panel">
      <div className="portal-panel-heading"><div><p className="portal-eyebrow">{plural(suite?.safety.length ?? 0, "check")} in the suite</p><h2>Safety events by type</h2></div><ProvenanceBadge provenance={run.provenance} now={now} /></div>
      <p className="portal-empty">Safety is not reported for this run yet.</p>
    </div>}
  </section>;
}

/* ---------- other run kinds ---------- */

const KIND_LABEL: Record<EvalRun["kind"], string> = { suite: "Evaluation suite run", "hil-latency": "Latency soak on bench hardware", timing: "Wall-clock timing experiment", episode: "Single recorded episode" };

/** For runs that are not suite runs: what kind of run it is and its note. */
export function AboutRun({ run, robot, now }: { run: EvalRun; robot: Robot; now: number | null }) {
  const duration = fmtDuration(secondsBetween(run.startedAt, run.finishedAt));
  return <section className="cfg-section portal-panel" aria-labelledby="ev-about-title">
    <div className="portal-panel-heading"><div><p className="portal-eyebrow">{KIND_LABEL[run.kind]}</p><h2 id="ev-about-title">About this run</h2></div><ProvenanceBadge provenance={run.provenance} now={now} /></div>
    <FactList facts={[
      { label: "Purpose", value: run.purpose ?? NOT_REPORTED },
      { label: "Variant", value: run.variant, detail: `On ${robot.name}` },
      { label: run.kind === "hil-latency" ? "Requests" : "Episodes", value: run.counts.successes === null ? fmtCount(run.counts.episodes) : `${fmtRatio(run.counts.successes, run.counts.episodes)} ${run.kind === "hil-latency" ? "scored correct" : "succeeded"}` },
      { label: "Wall-clock duration", value: duration ?? NOT_REPORTED, detail: run.startedAt ? `Started ${fmtDateTime(run.startedAt)}` : undefined },
      ...(run.note ? [{ label: "Note", value: run.note }] : []),
      { label: "Evidence", value: <ProvenanceBadge provenance={run.provenance} now={now} />, detail: provenanceDetail(run.provenance) },
    ]} />
  </section>;
}

/** Nothing has finished yet: the gate above lists what will be checked. */
export function NoResultsYet({ run }: { run: EvalRun }) {
  return <section className="cfg-section" aria-label="Results">
    <EmptyState icon="clock" title={run.status === "queued" ? "This run has not started." : "No results yet."}
      text="Results by task, scenario slices, failure modes and rollouts appear here as episodes finish." />
  </section>;
}

/* ---------- recorded evidence from the control plane ---------- */

export function recordedProvenance(evaluation: EvaluationRun): StoredProvenance {
  return { kind: "recorded", at: evaluation.updated_at, n: evaluation.report?.case_count ?? evaluation.cases.length, source: `Control-plane evaluation ${evaluation.id}` };
}

/** The real evaluation a run links to (`recordedEvaluationId`): its report and the case episodes to replay. */
export function RecordedEvaluationPanel({ evaluationId, state, onRetry, replayHref, onOpenReplay }: {
  evaluationId: string; state: Remote<EvaluationRun>; onRetry: () => void; replayHref: (episodeId: string) => string; onOpenReplay: (event: MouseEvent<HTMLAnchorElement>) => void;
}) {
  let body: ReactNode;
  let badge: ReactNode = null;
  if (state.status === "loading" || state.status === "idle") body = <p className="portal-loading" role="status">Reading the recorded evaluation…</p>;
  else if (state.status === "error") {
    body = <Notice tone="warning" action={<button className="btn btn-secondary cfg-btn" type="button" onClick={onRetry}>Try again</button>}>
      {state.missing ? `Evaluation ${evaluationId} is not available to this account.` : `The recorded evaluation could not be read. ${state.message}`}
    </Notice>;
  } else {
    const evaluation = state.data, report = evaluation.report;
    badge = <span className="cfg-badges"><Badge tone={report ? (report.passed ? "success" : "warning") : "neutral"}>{report ? (report.passed ? "Passed" : "Did not pass") : evaluation.state.replaceAll("_", " ")}</Badge><ProvenanceBadge provenance={recordedProvenance(evaluation)} /></span>;
    type Case = { seed: number; episode: string | null; state: string; passed: boolean | null; steps: number | null; wall: number | null };
    const cases: Case[] = report
      ? report.cases.map(item => ({ seed: item.seed, episode: item.episode_id, state: item.state, passed: item.evidence_valid ? item.passed : null, steps: item.steps, wall: item.wall_duration_s }))
      : evaluation.cases.map(item => ({ seed: item.seed, episode: item.episode_id, state: "reported", passed: null, steps: null, wall: null }));
    const reportedScope: unknown = report ? (report as { scope?: unknown }).scope : null;
    const scope = typeof reportedScope === "string" && reportedScope ? reportedScope : null;
    body = <>
      <p className="ev-recorded__summary">{report
        ? <><strong>{report.passed ? "Passed" : "Did not pass"}</strong> · {report.successes} / {report.case_count} successful cases; {report.min_successes} required · median case wall time {report.median_wall_s === null ? NOT_REPORTED : `${report.median_wall_s.toFixed(1)} s`}</>
        : <>{evaluation.cases.filter(item => item.episode_id).length} / {evaluation.cases.length} case reports received. A score appears when the job records its outcome.</>}</p>
      {scope && <p className="portal-context-note">Scope: {scope.replaceAll("_", " ")}.</p>}
      <DataTable<Case> label="Recorded evaluation cases" rows={cases} rowKey={item => `${item.seed}-${item.episode ?? "none"}`} caption={`Cases of ${evaluation.id} · ${evaluation.detail}`}
        columns={[
          { key: "seed", header: "Seed", numeric: true, cell: item => item.seed },
          { key: "state", header: "Mission outcome", cell: item => item.state.replaceAll("_", " ") },
          { key: "result", header: "Task result", cell: item => item.passed === null ? <NotReported /> : <OutcomeBadge outcome={item.passed ? "succeeded" : "failed"} /> },
          { key: "steps", header: "Steps", numeric: true, cell: item => item.steps === null ? NOT_REPORTED : fmtCount(item.steps) },
          { key: "wall", header: "Wall time", numeric: true, cell: item => item.wall === null ? NOT_REPORTED : `${item.wall.toFixed(1)} s` },
          { key: "replay", header: "Replay", srOnly: true, cell: item => item.episode ? <Link className="portal-table-link" href={replayHref(item.episode)} data-replay-link={item.episode} onClick={onOpenReplay} aria-label={`Replay ${item.episode}`}>Replay ▸</Link> : "No episode" },
        ]} />
    </>;
  }
  return <section className="cfg-section portal-panel" aria-labelledby="ev-recorded-eval-title">
    <div className="portal-panel-heading"><div><p className="portal-eyebrow">Control-plane evaluation · {evaluationId}</p><h2 id="ev-recorded-eval-title">Recorded evaluation</h2></div>{badge}</div>
    {body}
  </section>;
}

/** A run that is one published episode (`recordedEpisodeId`). */
export function RecordedEpisodePanel({ run, href, onOpenReplay, now }: { run: EvalRun; href: string; onOpenReplay: (event: MouseEvent<HTMLAnchorElement>) => void; now: number | null }) {
  const episode = run.recordedEpisodeId!;
  return <section className="cfg-section portal-panel" aria-labelledby="ev-recorded-episode-title">
    <div className="portal-panel-heading"><div><p className="portal-eyebrow">Published recording · {episode}</p><h2 id="ev-recorded-episode-title">Recorded episode</h2></div><ProvenanceBadge provenance={run.provenance} now={now} /></div>
    <p className="ev-recorded__summary">Camera frames and applied actions recorded by the coordinator for this run’s episode, replayed from the control plane.</p>
    <Link className="btn btn-secondary cfg-btn" href={href} data-replay-link={episode} onClick={onOpenReplay}>Replay {episode} ▸</Link>
  </section>;
}
