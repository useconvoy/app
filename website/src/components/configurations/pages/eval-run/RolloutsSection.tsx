"use client";

import Link from "next/link";
import { useState } from "react";
import type { MouseEvent } from "react";
import { rolloutsFor, sliceInfo, taskName } from "@/lib/configurations/client";
import type { RolloutFilter } from "@/lib/configurations/client";
import { outcomeCounts, paginate, ROLLOUT_SORTS, rolloutNote, sortRollouts, type RolloutSort } from "@/lib/configurations/eval-run";
import { fmtCount, fmtSeconds, runLabel } from "@/lib/configurations/format";
import type { ConvoyWorkspace, EvalRun, EvalSuite, Rollout } from "@/lib/configurations/types";
import { OutcomeBadge, ProvenanceBadge, Tags } from "../../Badges";
import { DataTable, type Column } from "../../DataTable";
import { FilterChips, SelectField, Toolbar } from "../../Toolbar";
import type { RunQuery } from "./hooks";

export const ROLLOUT_PAGE_SIZE = 10;
const OUTCOMES: readonly RolloutFilter[] = ["all", "failed", "safety"];

/** Filters read from the URL; unknown slices or tasks are ignored rather than showing an empty table. */
export function rolloutFilters(query: RunQuery, suite: EvalSuite | null): { outcome: RolloutFilter; sliceId: string | null; taskId: string | null } {
  const outcome = query.get("outcome");
  const slice = query.get("slice"), task = query.get("task");
  return {
    outcome: OUTCOMES.includes(outcome as RolloutFilter) ? outcome as RolloutFilter : "all",
    sliceId: slice && suite?.sliceFamilies.some(family => family.slices.some(item => item.id === slice)) ? slice : null,
    taskId: task && suite?.tasks.some(item => item.id === task) ? task : null,
  };
}

/**
 * The run's stored rollouts: outcome chips (counts follow the slice and task
 * filters), slice and task filters in the URL, a sort, ten rows per page, and a
 * replay link per row (`?rollout=`).
 */
export function RolloutsSection({ workspace, run, suite, query, replayHref, onOpenReplay, now }: {
  workspace: ConvoyWorkspace; run: EvalRun; suite: EvalSuite | null; query: RunQuery; replayHref: (id: string) => string;
  onOpenReplay: (event: MouseEvent<HTMLAnchorElement>, id: string) => void; now: number | null;
}) {
  const { outcome, sliceId, taskId } = rolloutFilters(query, suite);
  const [sort, setSort] = useState<RolloutSort>("events");
  const filterKey = [outcome, sliceId, taskId, sort].join("|");
  const [paging, setPaging] = useState({ key: filterKey, page: 0 });
  const all = rolloutsFor(workspace, run.id);
  const counts = outcomeCounts(rolloutsFor(workspace, run.id, { sliceId, taskId }));
  const filtered = sortRollouts(rolloutsFor(workspace, run.id, { outcome, sliceId, taskId }), sort);
  const view = paginate(filtered, paging.key === filterKey ? paging.page : 0, ROLLOUT_PAGE_SIZE);
  const kinds = new Set(all.map(rollout => rollout.provenance.kind));
  const common = kinds.size === 1 ? all[0].provenance : null;
  const rates = new Set(all.map(rollout => rollout.rateHz));
  const rate = rates.size === 1 ? all[0]?.rateHz ?? null : null;
  const sliceName = sliceId ? sliceInfo(suite, sliceId).name : null;
  const filtering = outcome !== "all" || !!sliceId || !!taskId;
  const clear = () => query.update({ outcome: null, slice: null, task: null });
  const go = (page: number) => { if (page >= 0 && page < view.pages && page !== view.page) setPaging({ key: filterKey, page }); };
  const replayLink = (rollout: Rollout, label?: string) => <Link className={label ? "portal-table-link" : "portal-table-link cfg-row-link"} href={replayHref(rollout.id)}
    data-replay-link={rollout.id} aria-label={label ? `Replay ${rollout.id}` : undefined} onClick={event => onOpenReplay(event, rollout.id)}>{label ?? rollout.id}</Link>;
  const columns: Array<Column<Rollout>> = [
    { key: "episode", header: "Episode", cell: rollout => replayLink(rollout) },
    { key: "task", header: "Task", cell: rollout => taskName(suite, rollout.taskId) },
    { key: "slice", header: "Slice", cell: rollout => <Tags items={rollout.sliceIds.map(id => sliceInfo(suite, id).name)} /> },
    { key: "seed", header: "Seed", numeric: true, cell: rollout => rollout.seed },
    { key: "outcome", header: "Outcome", wrap: true, cell: rollout => <OutcomeBadge outcome={rollout.outcome} />, detail: rolloutNote },
    { key: "violations", header: "Violations", numeric: true, cell: rollout => rollout.violations.length, detail: rollout => rollout.violations.map(item => item.checkId).join(", ") || null },
    { key: "duration", header: "Duration", numeric: true, cell: rollout => fmtSeconds(rollout.durationS) },
    { key: "steps", header: "Steps", numeric: true, cell: rollout => fmtCount(rollout.steps) },
    ...(common ? [] : [{ key: "evidence", header: "Evidence", cell: (rollout: Rollout) => <ProvenanceBadge provenance={rollout.provenance} now={now} /> }]),
    { key: "replay", header: "Replay", srOnly: true, cell: rollout => replayLink(rollout, "Replay ▸") },
  ];
  const scope = [
    outcome === "failed" ? "that did not succeed" : outcome === "safety" ? "with a safety event" : null,
    sliceName ? `in ${sliceName}` : null,
    taskId ? `on ${taskName(suite, taskId)}` : null,
  ].filter(Boolean).join(", ");
  const sortLabel = ROLLOUT_SORTS.find(item => item.value === sort)?.label.toLowerCase() ?? sort;
  return <section className="cfg-section" id="rollouts" aria-labelledby="ev-rollouts-title">
    <div className="portal-section-label"><h2 id="ev-rollouts-title">Sim rollouts</h2>
      <span className="cfg-inline">Episodes of {runLabel(run)} stored in the workspace · durations in simulated seconds{rate ? ` · ${rate} Hz steps` : ""}{common && <ProvenanceBadge provenance={common} now={now} />}</span></div>
    <div className="portal-panel">
      {all.length === 0 ? <p className="portal-empty">{run.status === "running" || run.status === "queued" ? `No rollouts are stored for ${runLabel(run)} yet.` : `No rollouts are stored for ${runLabel(run)}.`}</p> : <>
        <Toolbar>
          <FilterChips<RolloutFilter> label="Filter rollouts by outcome" value={outcome} onChange={id => query.update({ outcome: id === "all" ? null : id })}
            options={[{ id: "all", label: "All", count: counts.all }, { id: "failed", label: "Failed", count: counts.failed }, { id: "safety", label: "Safety events", count: counts.safety }]} />
          {suite && suite.sliceFamilies.length > 0 && <SelectField label="Slice" value={sliceId ?? ""} onChange={value => query.update({ slice: value || null })}
            options={[{ value: "", label: "All slices" }, ...suite.sliceFamilies.flatMap(family => family.slices.map(slice => ({ value: slice.id, label: `${family.name} · ${slice.name}` })))]} />}
          {suite && suite.tasks.length > 0 && <SelectField label="Task" value={taskId ?? ""} onChange={value => query.update({ task: value || null })}
            options={[{ value: "", label: "All tasks" }, ...suite.tasks.map(task => ({ value: task.id, label: task.name }))]} />}
          <SelectField<RolloutSort> label="Sort" end value={sort} onChange={setSort} options={ROLLOUT_SORTS} />
        </Toolbar>
        <DataTable label="Sim rollouts" rows={view.items} rowKey={rollout => rollout.id} columns={columns} rowClass={rollout => rollout.violations.length ? "cfg-tr--flag" : undefined}
          caption={`${filtering ? `${fmtCount(filtered.length)} of ${fmtCount(all.length)}` : `All ${fmtCount(all.length)}`} rollouts stored for ${runLabel(run)}${scope ? ` ${scope}` : ""} (the run has ${fmtCount(run.counts.episodes)} episodes) · ${sortLabel}`}
          empty={<>No stored rollouts match these filters. <button className="portal-table-link" type="button" onClick={clear}>Clear filters</button></>} />
        <div className="ev-pager">
          <span aria-live="polite">{view.total ? `Showing ${view.from}–${view.to} of ${fmtCount(view.total)} · page ${view.page + 1} of ${view.pages}` : "Showing 0 rollouts"}</span>
          <span className="cfg-inline">
            <button className="btn btn-secondary cfg-btn" type="button" aria-disabled={view.page === 0} onClick={() => go(view.page - 1)}>Previous</button>
            <button className="btn btn-secondary cfg-btn" type="button" aria-disabled={view.page >= view.pages - 1} onClick={() => go(view.page + 1)}>Next</button>
          </span>
        </div>
      </>}
    </div>
  </section>;
}
