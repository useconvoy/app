"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { getSuite, latestGateRun, runHref } from "@/lib/configurations/client";
import { revisionGate, safetyCaption } from "@/lib/configurations/dashboard";
import type { RobotRow } from "@/lib/configurations/dashboard";
import type { LiveBinding } from "@/lib/configurations/live";
import { fmtDate, fmtFixed, fmtNumber, runLabel } from "@/lib/configurations/format";
import { routes } from "@/lib/configurations/routes";
import type { CloudModel, ConfigRevision, Configuration, ConvoyWorkspace, EdgeModel, FlagRules, ModelRole } from "@/lib/configurations/types";
import { Badge, ProvenanceBadge, RunStatus } from "../../Badges";
import { CompatList } from "../../Compat";
import { FactList, Panel } from "../../Facts";
import type { FactItem } from "../../Facts";
import { FilterChips } from "../../Toolbar";

type LiveMap = Readonly<Record<string, LiveBinding | undefined>>;
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
const ROLE: Record<ModelRole, string> = { planner: "planner", policy: "policy", "fallback-policy": "fallback", "skill-pack": "skill pack", verifier: "verifier" };
const ROUTE_MODE = { hybrid: "Hybrid", "cloud-only": "Cloud only", "edge-only": "Edge only" } as const;
const sentence = (text: string) => text ? `${text[0].toUpperCase()}${text.slice(1)}` : text;

/** Revision status words: "r4 testing · r3 in production". */
export function revisionStatus(config: Configuration, rev: string): string | null {
  return rev === config.productionRev ? "in production" : rev === config.candidateRev ? (config.status === "draft" ? "draft" : "testing") : null;
}
function revisionsLine(config: Configuration): string {
  const parts = [config.candidateRev && `${config.candidateRev} ${config.status === "draft" ? "draft" : "testing"}`, config.productionRev && `${config.productionRev} in production`].filter(Boolean);
  return parts.length ? parts.join(" · ") : `${config.revisions.at(-1)?.rev ?? ""}`;
}

function modelBadge(model: EdgeModel | CloudModel): ReactNode {
  if (model.state === "proposed") return <Badge>Proposed</Badge>;
  if (model.state === "blocked") return <Badge tone="warning" icon="blocked">Blocked</Badge>;
  if (model.state === "not-deployed") return <Badge>Not deployed</Badge>;
  return model.evidence ? <ProvenanceBadge provenance={model.evidence} /> : null;
}
function modelFacts(config: Configuration, revision: ConfigRevision): FactItem[] {
  const edge = revision.edgeModels.map(model => ({
    label: `Edge ${ROLE[model.role]}`, value: <span className="cfg-inline">{model.name}{modelBadge(model)}</span>,
    detail: [model.runtime, finite(model.contextTokens) ? `${fmtNumber(model.contextTokens, 0)}-token context` : null, model.detail].filter(Boolean).join(" · "),
  }));
  const cloud = revision.cloudModels.map(model => ({
    label: `Cloud ${ROLE[model.role]}`, value: <span className="cfg-inline">{model.name}{modelBadge(model)}</span>,
    detail: [model.serving, model.detail].filter(Boolean).join(" · "),
  }));
  const chunk = revision.cloudModels.find(model => model.chunk)?.chunk;
  const created = revision.createdAt ? `created ${fmtDate(revision.createdAt)}` : null;
  return [
    ...edge, ...cloud,
    ...(chunk ? [{ label: "Action chunks", value: `${fmtNumber(chunk.actions, 0)} actions @ ${fmtNumber(chunk.rateHz)} Hz`, detail: `Valid ${fmtNumber(chunk.validityMs, 0)} ms from the observation; late actions never run` }] : []),
    ...(!edge.length && !cloud.length ? [{ label: "Models", value: "None declared" }] : []),
    { label: "Revisions", value: revisionsLine(config), detail: [revision.note && `${revision.rev}: ${revision.note}`, created].filter(Boolean).join(" · ") || undefined },
  ];
}

function deviceFacts(revision: ConfigRevision, rows: readonly RobotRow[], live: LiveMap): FactItem[] {
  const hardware = revision.edgeHardware;
  const mode = hardware.powerModes.find(item => item.id === hardware.powerModeId);
  const others = hardware.powerModes.filter(item => item.id !== hardware.powerModeId).map(item => item.label);
  const resident = revision.edgeModels.filter(model => finite(model.residentGiB) && model.state !== "blocked" && model.state !== "not-deployed");
  // What a live-bound robot's device reports now (measured); stored robots only declare.
  const reporter = rows.find(row => row.bound && row.reporting);
  const data = reporter ? live[reporter.robot.id]?.data ?? null : null;
  const software = [data?.hardware?.l4tRelease && `L4T ${data.hardware.l4tRelease}`, data?.hardware?.cudaVersion && `CUDA ${data.hardware.cudaVersion}`].filter(Boolean).join(" · ");
  const measured = reporter?.readings.provenance;
  const agents = new Set(rows.filter(row => !row.bound).map(row => row.robot.agentVersion).filter((version): version is string => !!version));
  return [
    { label: "Edge hardware", value: hardware.name, detail: hardware.compute },
    {
      label: "Memory", value: `${fmtNumber(hardware.memoryGiB)} GiB usable, shared CPU/GPU`,
      detail: finite(hardware.edgeBudgetGiB) ? `Edge budget ${fmtNumber(hardware.edgeBudgetGiB)} GiB${resident.length ? `: ${resident.map(model => `${ROLE[model.role]} ${fmtNumber(model.residentGiB)}`).join(", ")} GiB` : ""}` : hardware.memoryNote,
    },
    { label: "Power mode", value: mode?.label ?? "Not reported", detail: [others.length ? `Also ${others.join(" and ")}` : null, mode?.note].filter(Boolean).join(" · ") || undefined },
    software && reporter
      ? { label: "JetPack / L4T", value: <span className="cfg-inline">{software}<ProvenanceBadge provenance={measured} /></span>, detail: `Reported by ${reporter.robot.name}${hardware.software ? ` · declared ${hardware.software}` : ""}` }
      : { label: "JetPack / L4T", value: hardware.software ?? "Not reported", detail: hardware.software ? "Declared for this configuration" : undefined },
    data?.agentVersion && reporter
      ? { label: "Agent", value: <span className="cfg-inline">{data.agentVersion}<ProvenanceBadge provenance={measured} /></span>, detail: `Reported by ${reporter.robot.name}${finite(data.heartbeatIntervalS) ? ` · heartbeat every ${fmtNumber(data.heartbeatIntervalS)} s` : ""}` }
      : { label: "Agent", value: agents.size ? [...agents].join(", ") : "Not reported", detail: agents.size ? "As registered by the robots" : undefined },
    {
      label: "Thermal limits", value: [finite(hardware.thermal.swThrottleC) && `Software throttle ${fmtFixed(hardware.thermal.swThrottleC)} °C`, finite(hardware.thermal.hwThrottleC) && `hardware throttle ${fmtFixed(hardware.thermal.hwThrottleC)} °C`].filter(Boolean).join(" · ") || "Not reported",
      detail: [finite(hardware.thermal.shutdownC) && `Shutdown ${fmtFixed(hardware.thermal.shutdownC)} °C`, hardware.thermal.sensor].filter(Boolean).join(" · "),
    },
    { label: "Telemetry", value: "CPU, GPU, memory, board input, SoC temperature", detail: "Per-rail power, fan speed and the active power mode are not reported by the agent" },
  ];
}
function flagRuleText(rules: FlagRules): { attention: string; warning: string } {
  const attention = [
    finite(rules.socTempAttentionC) && `SoC ≥ ${fmtNumber(rules.socTempAttentionC)} °C`,
    finite(rules.fallbackAttentionPct) && `fallback > ${fmtNumber(rules.fallbackAttentionPct)} % of chunks`,
    finite(rules.reportAttentionS) && `no report for ${fmtNumber(rules.reportAttentionS)} s`,
  ].filter(Boolean).join(", ");
  const warning = [
    finite(rules.socTempWarnC) && `SoC ≥ ${fmtNumber(rules.socTempWarnC)} °C`,
    finite(rules.boardPowerWarnPctOfCap) && `board power peaks ≥ ${fmtNumber(rules.boardPowerWarnPctOfCap)} % of the power mode`,
    finite(rules.memoryAvailableWarnPct) && `memory available < ${fmtNumber(rules.memoryAvailableWarnPct)} %`,
    finite(rules.edgeP95BudgetMs) && `edge p95 > ${fmtNumber(rules.edgeP95BudgetMs, 0)} ms`,
    finite(rules.cloudTimeoutBudgetPct) && `cloud timeouts > ${fmtNumber(rules.cloudTimeoutBudgetPct)} %`,
  ].filter(Boolean).join(", ");
  return { attention, warning };
}

function routingFacts(revision: ConfigRevision): FactItem[] {
  const { routing } = revision;
  const trigger = routing.fallbackTrigger, action = routing.fallbackAction, escalation = routing.escalation;
  const otherwise = action.otherwise === "safe-hold" ? "safe hold" : "pause and escalate";
  const or = [finite(trigger.packetLossPct) && `packet loss > ${fmtNumber(trigger.packetLossPct)} %`, finite(trigger.missedDeadlines) && `${fmtNumber(trigger.missedDeadlines, 0)} consecutive chunk deadline misses`].filter(Boolean);
  return [
    { label: "Default route", value: `${ROUTE_MODE[routing.mode]} · ${routing.summary}`, detail: routing.defaultRoute },
    { label: "Fallback trigger", value: finite(trigger.cloudRttP95Ms) ? `Cloud RTT p95 > ${fmtNumber(trigger.cloudRttP95Ms, 0)} ms${finite(trigger.windowS) ? ` over ${fmtNumber(trigger.windowS)} s` : ""}` : "No cloud fallback trigger", detail: or.length ? `Or ${or.join(", or ")}` : undefined },
    { label: "Fallback action", value: action.blendInto ? `Finish valid actions, then ${action.blendInto}${finite(action.speedFactor) ? ` at ${fmtNumber(action.speedFactor)}×` : ""}` : sentence(otherwise), detail: `${finite(action.validityMs) ? `Actions valid ${fmtNumber(action.validityMs, 0)} ms from the observation; ` : ""}otherwise ${otherwise}` },
    { label: "Escalation", value: [finite(escalation.stallS) && `Stall > ${fmtNumber(escalation.stallS)} s`, finite(escalation.failedGrasps) && `${fmtNumber(escalation.failedGrasps, 0)} failed grasps`, escalation.onVerifierAnomaly === "escalate" && "verifier anomaly"].filter(Boolean).join(", ") || "Not set", detail: `Channel: ${escalation.channel}` },
  ];
}
function safetyFacts(ws: ConvoyWorkspace, config: Configuration, revision: ConfigRevision): FactItem[] {
  const guard = revision.routing.thermalGuard;
  const definitions = revision.safety.definitions;
  const critical = definitions.filter(item => item.severity === "critical").map(item => `${item.id} ${item.name.toLowerCase()}`);
  const rules = flagRuleText(revision.flagRules);
  const suite = getSuite(ws, config.suiteId);
  const gate = revisionGate(ws, config, revision.rev);
  return [
    { label: "Thermal & power guard", value: [finite(guard.socTempC) && `SoC ≥ ${fmtNumber(guard.socTempC)} °C`, finite(guard.overCurrentEventsPer10Min) && `≥ ${fmtNumber(guard.overCurrentEventsPer10Min, 0)} over-current events in 10 min`].filter(Boolean).join(", or ") || "Not set", detail: guard.action },
    { label: "Safety envelope", value: `${revision.safety.name} · ${definitions.length ? `${definitions[0].id}–${definitions.at(-1)?.id}` : "no checks"}${critical.length ? ` · ${critical.join(", ")} critical` : ""}`, detail: revision.safety.note },
    { label: "Flag rules", value: rules.attention ? `Needs attention: ${rules.attention}` : "No attention rules", detail: `${rules.warning ? `Warning: ${rules.warning}. ` : ""}Display rules Convoy evaluates on reported telemetry; manual flags add a note.` },
    {
      label: "Promotion gate", value: suite ? suite.gate.slice(0, 3).map(item => `${item.label} ${item.target}`).join(" · ") : "No suite set",
      detail: suite ? `${suite.gate.slice(3).map(item => `${item.label} ${item.target}`).join(" · ")}${suite.gate.length > 3 ? " · " : ""}${gate.reason}` : undefined,
    },
  ];
}

function EditLink({ config, what }: { config: Configuration; what: string }) {
  return <Link className="cfg-btn-text" href={routes.newConfiguration(config.id)}>Edit<span className="cfg-sr"> {what}</span></Link>;
}

/** Model metadata, device metadata and routing & safety for one revision (the Overview shows the current revision). */
export function SpecificationPanels({ workspace, config, revision, rows, live, onViewAll }: { workspace: ConvoyWorkspace; config: Configuration; revision: ConfigRevision; rows: readonly RobotRow[]; live: LiveMap; onViewAll?: () => void }) {
  const status = revisionStatus(config, revision.rev);
  return <>
    <div className="portal-two-column">
      <Panel eyebrow={`Specification · ${revision.rev}${status ? ` · ${status}` : ""}`} title="Model metadata" titleId={`cd-model-${revision.rev}`} action={<EditLink config={config} what="model metadata" />}>
        <FactList facts={modelFacts(config, revision)} className="cd-facts" />
      </Panel>
      <Panel eyebrow="Specification · edge hardware" title="Device metadata" titleId={`cd-device-meta-${revision.rev}`} action={<EditLink config={config} what="device metadata" />}>
        <FactList facts={deviceFacts(revision, rows, live)} className="cd-facts" />
      </Panel>
    </div>
    <Panel eyebrow={`Specification · ${revision.rev}`} title="Routing & safety" titleId={`cd-routing-${revision.rev}`}
      action={onViewAll ? <button className="cfg-btn-text" type="button" onClick={onViewAll}>View safety checks</button> : <EditLink config={config} what="routing and safety" />}>
      <div className="portal-two-column cd-columns">
        <FactList facts={routingFacts(revision)} className="cd-facts" />
        <FactList facts={safetyFacts(workspace, config, revision)} className="cd-facts" />
      </div>
    </Panel>
  </>;
}

/** Overview: the declared configuration at the revision under test, with a link to the full specification. */
export function SpecificationSummary({ workspace, config, revision, rows, live, onViewAll }: { workspace: ConvoyWorkspace; config: Configuration; revision: ConfigRevision; rows: readonly RobotRow[]; live: LiveMap; onViewAll: () => void }) {
  return <section className="cfg-section" aria-labelledby="cd-spec-title">
    <div className="portal-section-label"><h2 id="cd-spec-title">Specification</h2><span>Declared configuration · {revisionsLine(config)}</span></div>
    <SpecificationPanels workspace={workspace} config={config} revision={revision} rows={rows} live={live} onViewAll={onViewAll} />
  </section>;
}

/**
 * The Specification tab: a revision switch, the three panels, the safety checks,
 * the compatibility check and the revision history with each revision's gate.
 */
export function SpecificationFull({ workspace, config, revision, rows, live, onRevision, now }: { workspace: ConvoyWorkspace; config: Configuration; revision: ConfigRevision; rows: readonly RobotRow[]; live: LiveMap; onRevision: (rev: string) => void; now: number | null }) {
  const revisions = config.revisions.toReversed();
  return <div className="cfg-section cd-spec">
    <div className="cd-spec__head">
      <div className="portal-section-label"><h2>Specification · {revision.rev}</h2><span>Declared configuration · {revisionsLine(config)}</span></div>
      {revisions.length > 1 && <div className="cfg-field"><span aria-hidden="true">Revision</span>
        <FilterChips label="Revision" value={revision.rev} onChange={onRevision}
          options={revisions.map(item => ({ id: item.rev, label: `${item.rev}${revisionStatus(config, item.rev) ? ` · ${revisionStatus(config, item.rev)}` : ""}` }))} />
      </div>}
    </div>
    <SpecificationPanels workspace={workspace} config={config} revision={revision} rows={rows} live={live} />
    <Panel eyebrow={`${revision.safety.name} · ${revision.rev}`} title="Safety checks" titleId="cd-safety-title">
      <div className="portal-table-scroll" tabIndex={0} aria-label="Safety checks, scroll horizontally">
        <table className="portal-table cfg-table">
          <caption>{safetyCaption(revision.safety.note)}</caption>
          <thead><tr><th scope="col">Check</th><th scope="col">Name</th><th scope="col">Rule</th><th scope="col">Severity</th></tr></thead>
          <tbody>{revision.safety.definitions.map(item => <tr key={item.id}>
            <th scope="row">{item.id}</th><td className="cfg-wrap">{item.name}</td><td className="cfg-wrap">{item.rule}{item.criticalWhen && <small>{item.criticalWhen}</small>}</td>
            <td><Badge tone={item.severity === "minor" ? "neutral" : "warning"}>{sentence(item.severity)}</Badge></td>
          </tr>)}</tbody>
        </table>
      </div>
    </Panel>
    <Panel eyebrow={`Edge hardware fit · ${revision.rev}`} title="Compatibility check" titleId="cd-compat-title">
      {revision.compatibility.length ? <CompatList checks={revision.compatibility} now={now} /> : <p className="portal-empty">No compatibility checks recorded for this revision.</p>}
    </Panel>
    <Panel eyebrow="Newest first" title="Revisions" titleId="cd-revisions-title">
      <div className="portal-table-scroll" tabIndex={0} aria-label="Revisions, scroll horizontally">
        <table className="portal-table cfg-table">
          <thead><tr><th scope="col">Revision</th><th scope="col">Created</th><th scope="col">Change</th><th scope="col">Status</th><th scope="col">Latest gate run</th></tr></thead>
          <tbody>{revisions.map(item => {
            const run = latestGateRun(workspace, config.id, item.rev);
            const href = run ? runHref(workspace, run) : null;
            const status = revisionStatus(config, item.rev);
            return <tr key={item.rev} className={item.rev === revision.rev ? "cfg-tr--current" : undefined}>
              <th scope="row"><button className="portal-table-link" type="button" aria-pressed={item.rev === revision.rev} onClick={() => onRevision(item.rev)}>{item.rev}</button></th>
              <td>{fmtDate(item.createdAt)}</td><td className="cfg-wrap">{item.note ?? "Not reported"}</td>
              <td>{status ? <Badge tone={status === "in production" ? "forest" : status === "testing" ? "info" : "neutral"}>{sentence(status)}</Badge> : "Superseded"}</td>
              <td>{run ? <span className="cfg-inline">{href ? <Link className="portal-table-link" href={href}>{runLabel(run)}</Link> : runLabel(run)}<RunStatus run={run} /><ProvenanceBadge provenance={run.provenance} /></span> : <span className="cfg-prov cfg-prov--none">Not evaluated</span>}</td>
            </tr>;
          })}</tbody>
        </table>
      </div>
    </Panel>
  </div>;
}
