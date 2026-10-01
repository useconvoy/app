"use client";

import type { ReactNode } from "react";
import { currentRevision } from "@/lib/configurations/client";
import { filterRobotRows, isSilent, needsAttention, robotFilterCounts, robotSortAccessor, ROBOT_SORT_FIRST, sortRobotRows } from "@/lib/configurations/dashboard";
import type { RobotFilter, RobotRow, RobotSort, RobotSortKey } from "@/lib/configurations/dashboard";
import { fmtDateTime, fmtMs, fmtNumber, fmtPct, fmtRelative, fmtUnit } from "@/lib/configurations/format";
import type { Configuration } from "@/lib/configurations/types";
import { HealthBadge, NotReported, ProvenanceBadge, RoleBadge } from "../../Badges";
import { DataTable } from "../../DataTable";
import type { Column } from "../../DataTable";
import { Panel } from "../../Facts";
import { Icon } from "../../Icons";
import { FilterChips } from "../../Toolbar";
import type { ChipOption } from "../../Toolbar";

const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
/** Columns CPU through Interventions (after Robot, Role, Rev, Health). */
const METRIC_COLUMNS = 9;
const CLOUD_COLUMNS = new Set(["cloud", "fallback", "interventions"]);
/** A metric cell: the formatted value, or "Not reported" (never 0 or a dash). */
const metric = (value: number | null | undefined, format: (value: number) => string): ReactNode => finite(value) ? format(value) : <NotReported />;

function lastReport(row: RobotRow, now: number | null): ReactNode {
  const seen = row.readings.lastSeenAt;
  if (!seen) return row.bound ? "No device report yet" : "No report yet";
  return <time dateTime={seen} title={fmtDateTime(seen)}>{now === null ? fmtDateTime(seen) : fmtRelative(seen, now)}</time>;
}
function silentReason(row: RobotRow): string {
  if (row.bound) return `Not reported · ${row.readings.healthReason ?? "no device report"}`;
  if (row.robot.kind === "simulator") return "Not reported · simulators send no device telemetry";
  return "Not reported · no telemetry or latency reported yet";
}

/** The flags column of the Robots tab: opens the flag dialog (the flags themselves show under Health). */
function FlagsCell({ row, onFlag }: { row: RobotRow; onFlag: (robotId: string) => void }) {
  const count = row.readings.flags.length;
  // Visible "1 flag" / "Flag"; the accessible name adds the verb and the robot ("Review 1 flag on Unit 08").
  return <button className="cfg-btn-text cd-flag-btn" type="button" onClick={() => onFlag(row.robot.id)}>
    {count ? <><Icon name="flag" small /><span className="cfg-sr">Review </span>{`${count} ${count === 1 ? "flag" : "flags"}`}<span className="cfg-sr"> on {row.robot.name}</span></>
      : <>Flag<span className="cfg-sr"> {row.robot.name}</span></>}
  </button>;
}
/** Under the health badge: the health reason, else the leading flag (a manual flag leaves a stored robot's health as it was). */
function healthDetail(row: RobotRow): ReactNode {
  if (row.readings.healthReason) return row.readings.healthReason;
  const flag = row.readings.flags[0];
  if (!flag) return null;
  const more = row.readings.flags.length - 1;
  return <span className="cfg-flagnote"><Icon name="flag" small />{flag.label}{more > 0 ? ` (and ${more} more)` : ""}</span>;
}

/**
 * The robots panel: filter chips (All, Test, Production, Needs attention) and the
 * sortable robots table. The live-bound robot stays pinned first; rows link to the
 * robot page; Needs attention rows are tinted. `onFlag` adds the flags column.
 */
export function RobotsPanel({ config, rows, filter, onFilter, sort, onSort, now, onFlag, onAdd, eyebrow = "Latest report per robot · device clock", titleId = "cd-robots-title" }: {
  config: Configuration; rows: readonly RobotRow[]; filter: RobotFilter; onFilter: (filter: RobotFilter) => void; sort: RobotSort; onSort: (sort: RobotSort) => void;
  now: number | null; onFlag?: (robotId: string) => void; onAdd: () => void; eyebrow?: string; titleId?: string;
}) {
  if (!rows.length) {
    return <div className="cfg-section"><Panel eyebrow={eyebrow} title="Robots" titleId={titleId}>
      <div className="cd-none"><p>No robots are attached to this configuration yet. Add a test robot to run its evaluation suite.</p>
        <button className="btn btn-secondary cfg-btn" type="button" onClick={onAdd}>Add robot</button></div>
    </Panel></div>;
  }
  const counts = robotFilterCounts(rows);
  const visible = sortRobotRows(filterRobotRows(rows, filter), sort);
  const hardware = currentRevision(config).edgeHardware;
  const windows = new Set(rows.filter(row => !row.bound).map(row => row.robot.latency?.window).filter(Boolean));
  const window = windows.size === 1 ? ` ${[...windows][0]}` : "";
  const bound = rows.filter(row => row.bound).map(row => row.robot.name);
  const chips: ReadonlyArray<ChipOption<RobotFilter>> = [
    { id: "all", label: "All", count: counts.all }, { id: "test", label: "Test", count: counts.test },
    { id: "production", label: "Production", count: counts.production }, { id: "attention", label: "Needs attention", count: counts.attention, icon: "flag" },
  ];
  const sortable = (key: RobotSortKey) => ({ sort: robotSortAccessor(key, sort.key === key ? sort.dir : ROBOT_SORT_FIRST[key]), firstDir: ROBOT_SORT_FIRST[key] });
  const evidence = (row: RobotRow) => <ProvenanceBadge provenance={row.readings.provenance} />;
  const columns: Array<Column<RobotRow>> = [
    { key: "name", header: "Robot", ...sortable("name"), cell: row => row.robot.name, detail: row => row.robot.site },
    { key: "role", header: "Role", cell: row => <RoleBadge role={row.robot.role} /> },
    { key: "rev", header: "Rev", cell: row => row.robot.rev ?? <NotReported /> },
    { key: "health", header: "Health", ...sortable("health"), cell: row => <HealthBadge health={row.readings.health} />, detail: healthDetail },
    { key: "cpu", header: "CPU", numeric: true, cell: row => metric(row.current?.cpuPct, value => fmtPct(value, 0)) },
    { key: "gpu", header: "GPU", numeric: true, cell: row => metric(row.current?.gpuPct, value => fmtPct(value, 0)) },
    { key: "memory", header: "Memory available", numeric: true, cell: row => metric(row.current?.memAvailableMiB, value => fmtUnit(value, "MiB", 0)) },
    { key: "temp", header: "SoC temp", numeric: true, ...sortable("temp"), cell: row => metric(row.current?.socTempC, value => fmtUnit(value, "°C", 1, true)) },
    { key: "power", header: "Board power", numeric: true, ...sortable("power"), cell: row => metric(row.current?.boardPowerW, value => fmtUnit(value, "W", 1, true)) },
    { key: "planner", header: "Planner p50", numeric: true, ...sortable("planner"), cell: row => metric(row.edge.ms?.p50, value => fmtMs(value)) },
    { key: "cloud", header: "Cloud e2e p50 / p95", numeric: true, cell: row => row.readings.cloudMs && finite(row.readings.cloudMs.p50) ? `${fmtNumber(row.readings.cloudMs.p50, 0)} / ${fmtNumber(row.readings.cloudMs.p95, 0)} ms` : <NotReported /> },
    { key: "fallback", header: `Fallback${window}`, numeric: true, ...sortable("fallback"), cell: row => metric(row.readings.fallbackPct, value => fmtPct(value)) },
    { key: "interventions", header: "Interventions / h", numeric: true, cell: row => metric(row.robot.interventions?.perHour, value => fmtNumber(value)) },
    { key: "evidence", header: "Evidence · last report", cell: evidence, detail: row => lastReport(row, now) },
    ...(onFlag ? [{ key: "flags", header: "Flags", cell: (row: RobotRow) => <FlagsCell row={row} onFlag={onFlag} /> }] : []),
  ];
  /** Same markup as DataTable's own cells. */
  const cellFor = (column: Column<RobotRow>, row: RobotRow) => {
    const detail = column.detail?.(row);
    return <td key={column.key} className={[column.numeric ? "cfg-num" : "", column.wrap ? "cfg-wrap" : ""].filter(Boolean).join(" ") || undefined}>
      {column.cell(row)}{detail !== null && detail !== undefined && detail !== "" && <small>{detail}</small>}
    </td>;
  };
  // A robot with nothing to show gets one cell with the reason; a live-bound robot without cloud figures gets one cell for those three columns.
  const customCells = (row: RobotRow) => {
    if (isSilent(row)) {
      return <>
        {columns.slice(1, 4).map(column => cellFor(column, row))}
        <td colSpan={METRIC_COLUMNS} className="cfg-wrap"><NotReported>{row.readings.healthReason ? "Not reported" : silentReason(row)}</NotReported></td>
        {columns.slice(4 + METRIC_COLUMNS).map(column => cellFor(column, row))}
      </>;
    }
    if (!row.bound || row.readings.cloudMs || finite(row.readings.fallbackPct) || finite(row.robot.interventions?.perHour)) return null;
    return <>{columns.slice(1).map(column => column.key === "cloud"
      ? <td key="cloud" colSpan={CLOUD_COLUMNS.size} className="cfg-wrap"><NotReported>Not reported · the device reports edge inference only</NotReported></td>
      : CLOUD_COLUMNS.has(column.key) ? null : cellFor(column, row))}</>;
  };
  const filterLabel = chips.find(chip => chip.id === filter)?.label ?? "All";
  return <div className="cfg-section">
    <Panel eyebrow={eyebrow} title="Robots" titleId={titleId} action={<FilterChips label="Filter robots" options={chips} value={filter} onChange={onFilter} />}>
      <DataTable caption={`${visible.length} of ${rows.length} ${rows.length === 1 ? "robot" : "robots"}${filter === "all" ? "" : ` · ${filterLabel}`}`} label="Robots" columns={columns} rows={visible}
        rowKey={row => row.robot.id} rowHref={row => row.href} rowClass={row => needsAttention(row) ? "cfg-tr--flag" : undefined}
        sort={sort} onSortChange={next => onSort({ key: next.key as RobotSortKey, dir: next.dir })} pinFirst={row => row.bound} rowCells={customCells}
        empty="No robots match this filter." className="cd-robots" />
      <p className="portal-context-note">Memory available of {fmtNumber(hardware.memoryGiB)} GiB shared · cloud e2e = observation to chunk received · fallback = share of cloud chunks{window ? `,${window}` : ""}{bound.length ? ` · ${bound.join(", ")} (live) stays pinned first` : ""}.</p>
    </Panel>
  </div>;
}
