"use client";

import { getRobot, logsFor } from "@/lib/configurations/client";
import { ALL_LOGS, filterLogs, logFacets, weakestProvenance } from "@/lib/configurations/dashboard";
import type { LogFilter, LogLevelFilter } from "@/lib/configurations/dashboard";
import { fmtCount } from "@/lib/configurations/format";
import type { Configuration, ConvoyWorkspace, LogLine } from "@/lib/configurations/types";
import { ProvenanceBadge } from "../../Badges";
import { Panel } from "../../Facts";
import { Icon } from "../../Icons";
import { LogPanel } from "../../LogPanel";
import { FilterChips, SelectField, Toolbar } from "../../Toolbar";

const PREVIEW_LINES = 8;
const LEVELS: ReadonlyArray<{ id: LogLevelFilter; label: string }> = [{ id: "all", label: "All" }, { id: "info", label: "Info" }, { id: "warn", label: "Warn" }, { id: "error", label: "Error" }];
const plural = (count: number, one: string, many = `${one}s`) => `${fmtCount(count)} ${count === 1 ? one : many}`;

function sourceLabel(ws: ConvoyWorkspace) {
  return (line: LogLine) => line.robotId ? `${line.source} · ${getRobot(ws, line.robotId)?.name ?? line.robotId}` : line.source;
}
const CLOCK_NOTE = "device clock, UTC";

/** Overview: the newest lines for this configuration and a way to the full log. */
export function LogsPreview({ workspace, config, onOpen }: { workspace: ConvoyWorkspace; config: Configuration; onOpen: () => void }) {
  const lines = logsFor(workspace, { configId: config.id });
  const shown = lines.slice(0, PREVIEW_LINES);
  const provenance = weakestProvenance(shown.map(line => line.provenance));
  return <div className="cfg-section">
    <Panel eyebrow={`Newest ${plural(shown.length, "line")} · ${CLOCK_NOTE}`} title="Model logs" titleId="cd-logs-preview-title"
      action={<div className="cfg-inline">{shown.length > 0 && <ProvenanceBadge provenance={provenance} />}<button className="cfg-btn-text" type="button" onClick={onOpen}>Open logs <Icon name="chevron-right" /></button></div>}>
      <LogPanel lines={shown} label="Model logs, newest first" sourceLabel={sourceLabel(workspace)} empty="No log lines are stored for this configuration." />
      {shown.length > 0 && <p className="portal-context-note">Newest first · {fmtCount(shown.length)} of {plural(lines.length, "line")} for this configuration · stored in the workspace; the connected device’s own logs are not collected here.</p>}
    </Panel>
  </div>;
}

/** The Logs tab: every stored line for this configuration with level, source and robot filters. */
export function LogsFull({ workspace, config, filter, onFilter }: { workspace: ConvoyWorkspace; config: Configuration; filter: LogFilter; onFilter: (filter: LogFilter) => void }) {
  const lines = logsFor(workspace, { configId: config.id });
  const facets = logFacets(lines);
  const sources = filter.source && !facets.sources.includes(filter.source) ? [...facets.sources, filter.source] : facets.sources;
  const visible = filterLogs(lines, filter);
  const provenance = weakestProvenance(visible.map(line => line.provenance));
  const robotName = (id: string) => getRobot(workspace, id)?.name ?? id;
  const filtered = filter.level !== "all" || !!filter.source || !!filter.robotId;
  return <div className="cfg-section">
    <Toolbar>
      <div className="cfg-field"><span aria-hidden="true">Level</span>
        <FilterChips label="Log level" value={filter.level} onChange={level => onFilter({ ...filter, level })} options={LEVELS.map(level => ({ ...level, count: facets.levels[level.id] }))} />
      </div>
      <SelectField label="Source" value={filter.source ?? ""} onChange={source => onFilter({ ...filter, source: source || null })}
        options={[{ value: "", label: "All sources" }, ...sources.map(source => ({ value: source, label: source }))]} />
      <SelectField label="Robot" value={filter.robotId ?? ""} onChange={robotId => onFilter({ ...filter, robotId: robotId || null })}
        options={[{ value: "", label: "All robots" }, ...facets.robotIds.map(id => ({ value: id, label: robotName(id) }))]} />
      {filtered && <button className="cfg-btn-text cfg-toolbar__end" type="button" onClick={() => onFilter(ALL_LOGS)}>Clear filters</button>}
    </Toolbar>
    <Panel eyebrow={`${fmtCount(visible.length)} of ${plural(lines.length, "line")} · newest first · ${CLOCK_NOTE}`} title="Model logs" titleId="cd-logs-title"
      action={visible.length > 0 ? <ProvenanceBadge provenance={provenance} /> : undefined}>
      <LogPanel lines={visible} label="Model logs, filtered, newest first" sourceLabel={sourceLabel(workspace)}
        empty={lines.length ? "No log lines match these filters." : "No log lines are stored for this configuration."} />
      <p className="portal-context-note">Lines stored in the workspace document for this configuration and its robots. The connected device’s own logs are not collected here.</p>
    </Panel>
  </div>;
}
