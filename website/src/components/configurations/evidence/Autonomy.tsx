"use client";

import type { ReactNode } from "react";
import { EVIDENCE_DEFINITIONS, INTERVENTION_LABEL } from "@/lib/configurations/evidence";
import type { Autonomy, InterventionRow } from "@/lib/configurations/evidence";
import { fmtCount, fmtFixed } from "@/lib/configurations/format";
import type { CallGroup, PlannerCalls } from "@/lib/configurations/slices";
import { Missing, Tag } from "../Badges";
import { EvidenceCard, type Definition, type Fact } from "./EvidenceCard";

const one = (value: number | null) => value === null ? null : fmtFixed(value, 1);
const plural = (n: number, word: string) => `${fmtCount(n)} ${word}${n === 1 ? "" : "s"}`;

/** Planner call results: a reply the executive refused is the model's; a device or transport failure is the link's. */
const CALL_DEFINITIONS = {
  valid: "A reply the executive accepted.",
  refused: "The model replied, the executive refused it: invalid choice, invalid JSON or invalid schema.",
  failed: "No reply came back: device error, HTTP or transport error, or timeout.",
} as const;
type CallKind = keyof typeof CALL_DEFINITIONS;
const CALL_LABEL: Record<CallKind, string> = { valid: "Valid reply", refused: "Refused by the executive", failed: "Device or transport failure" };
const CALL_SHORT: Record<CallKind, string> = { valid: "Valid", refused: "Refused", failed: "Device / transport" };
const CALL_PARTS: Record<string, string> = {
  planner_valid_replies: "valid", planner_invalid_choice: "invalid choice", planner_invalid_json: "invalid JSON", planner_invalid_schema: "invalid schema",
  planner_device_errors: "device errors", planner_http_errors: "HTTP or transport errors", planner_timeouts: "timeouts",
};
const CALL_KINDS: readonly CallKind[] = ["valid", "refused", "failed"];
/** A group's parts for a tooltip: "invalid choice 54 · invalid JSON 0 · invalid schema 0". */
const parts = (group: CallGroup) => group.parts.map(part => `${CALL_PARTS[part.name] ?? part.name} ${part.calls === null ? "not reported" : fmtCount(part.calls)}`).join(" · ");

const DEFINITIONS = [
  { term: "Intervention", text: EVIDENCE_DEFINITIONS.intervention },
  { term: "Autonomous episode", text: EVIDENCE_DEFINITIONS.autonomous },
  { term: "Decisions", text: EVIDENCE_DEFINITIONS.decisions },
  { term: "Failed decision", text: EVIDENCE_DEFINITIONS["failed-decision"] },
  { term: "Protective stop", text: EVIDENCE_DEFINITIONS["protective-stop"] },
  { term: "Unfinished episode", text: EVIDENCE_DEFINITIONS.unfinished },
  { term: "Arm–arm contact", text: EVIDENCE_DEFINITIONS["arm-arm-contact"] },
  { term: "First call", text: EVIDENCE_DEFINITIONS.firstCall },
] as const;

/** The definitions with the planner call results after Decisions, for a panel that shows them. */
const WITH_CALLS: readonly Definition[] = DEFINITIONS.flatMap((item): Definition[] => item.term === "Decisions"
  ? [item, { term: "Refused", text: CALL_DEFINITIONS.refused }, { term: "Device or transport failure", text: CALL_DEFINITIONS.failed }]
  : [item]);

/** One headline number: label, value and one short line; a missing value is a dash with the reason ("Not reported") as its line. */
function Kpi({ label, value, sub, title, missing = "Not reported" }: { label: string; value: string | null; sub?: ReactNode; title: string; missing?: string }) {
  return <div className="cv-au__kpi" title={title}>
    <dt>{label}</dt>
    <dd>
      <span className={`cv-au__value${value === null ? " cv-missing" : ""}`}>{value === null ? <span aria-hidden="true">–</span> : value}</span>
      <span className="cv-au__sub">{value === null ? missing : sub ?? " "}</span>
    </dd>
  </div>;
}

/** Events and episodes per intervention type; arm–arm contact (shown, not counted) last and muted. */
function Breakdown({ rows }: { rows: readonly InterventionRow[] }) {
  return <table className="cv-au__table">
    <caption className="cv-sr">Interventions by type</caption>
    <thead><tr><th scope="col">Intervention</th><th scope="col">Events</th><th scope="col">Episodes</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.type} className={row.counted ? undefined : "cv-au__uncounted"}>
      <th scope="row" title={EVIDENCE_DEFINITIONS[row.type]}>{INTERVENTION_LABEL[row.type]}{!row.counted && <Tag title={EVIDENCE_DEFINITIONS["arm-arm-contact"]}>Not counted</Tag>}</th>
      <td>{row.events === null ? <Missing /> : fmtCount(row.events)}</td>
      <td>{row.episodes === null ? <Missing /> : fmtCount(row.episodes)}</td>
    </tr>)}</tbody>
  </table>;
}

/** The compact tile's breakdown: events per type on one wrapped line; arm–arm contact muted, marked not counted. */
function BreakdownLine({ rows }: { rows: readonly InterventionRow[] }) {
  return <ul className="cv-au__line" aria-label="Interventions by type">{rows.map(row => <li key={row.type} className={row.counted ? undefined : "cv-au__uncounted"} title={EVIDENCE_DEFINITIONS[row.type]}>
    {INTERVENTION_LABEL[row.type]}<b>{row.events === null ? <Missing /> : fmtCount(row.events)}</b>{!row.counted && <span className="cv-au__note">not counted</span>}
  </li>)}</ul>;
}

/**
 * Planner calls by result, both kinds of unusable call always shown (a 0 too): replies the executive
 * refused (the model's) apart from device or transport failures (the link's).
 */
function CallsTable({ calls }: { calls: PlannerCalls }) {
  return <table className="cv-au__table">
    <caption className="cv-sr">Planner calls by result</caption>
    <thead><tr><th scope="col">Planner call</th><th scope="col">Calls</th><th scope="col">Episodes</th></tr></thead>
    <tbody>{CALL_KINDS.map(kind => <tr key={kind}>
      <th scope="row" title={kind === "valid" ? CALL_DEFINITIONS.valid : `${CALL_DEFINITIONS[kind]} ${parts(calls[kind])}`}>{CALL_LABEL[kind]}</th>
      <td>{calls[kind].calls === null ? <Missing /> : fmtCount(calls[kind].calls)}</td>
      <td>{calls[kind].episodes === null ? <Missing /> : fmtCount(calls[kind].episodes)}</td>
    </tr>)}</tbody>
  </table>;
}

/** The compact tile's planner calls on one wrapped line: valid, refused, device / transport. */
function CallsLine({ calls }: { calls: PlannerCalls }) {
  return <ul className="cv-au__calls" aria-label="Planner calls by result">{CALL_KINDS.map(kind => <li key={kind} title={kind === "valid" ? CALL_DEFINITIONS.valid : `${CALL_DEFINITIONS[kind]} ${parts(calls[kind])}`}>
    {CALL_SHORT[kind]}<b>{calls[kind].calls === null ? <Missing /> : fmtCount(calls[kind].calls)}</b>
  </li>)}</ul>;
}

/** Recorded context: the planner's calls and valid replies, and how many episodes report every counted type. */
function facts(autonomy: Autonomy, compact: boolean): Fact[] {
  const { calls, validReplies, reported, episodes } = autonomy;
  const items: Fact[] = [{
    label: "Planner calls",
    value: calls === null ? <Missing /> : `${fmtCount(calls)}${validReplies !== null && calls > 0 ? ` · ${fmtFixed(validReplies / calls * 100, 0)} % valid` : ""}`,
    title: EVIDENCE_DEFINITIONS.calls,
  }];
  if (!compact) items.push({ label: "Counts reported", value: `${fmtCount(reported)} of ${plural(episodes, "episode")}`, title: EVIDENCE_DEFINITIONS.reported });
  return items;
}

/**
 * Autonomy and teleop handoff across an eval's episodes: how many finished with no intervention, how
 * often an operator would have taken over (per episode, per 100 decisions, decisions between), the
 * first-call acceptance, the interventions by type, and the planner calls by result (`calls`, when
 * the episodes report them). `slice`: the slice the episodes are, named first in the scope line.
 * `compact`: the dashboard's version, with `source` (a link to the eval) in its foot.
 */
export function AutonomyPanel({ autonomy, calls = null, slice, source, compact = false }: { autonomy: Autonomy; calls?: PlannerCalls | null; slice?: string; source?: ReactNode; compact?: boolean }) {
  const { episodes, autonomous, autonomousShare, interventions, decisions } = autonomy;
  const autonomousValue = autonomous === null ? null : `${fmtCount(autonomous)} / ${fmtCount(episodes)}`;
  const autonomousSub = autonomousShare === null ? undefined : `${fmtFixed(autonomousShare * 100, 0)} %`;
  const noneCounted = interventions === 0 && decisions !== null;
  const scope = `${slice ? `${slice} · teleop handoffs` : "Teleop handoffs"} · ${plural(episodes, "episode")}${decisions === null ? "" : ` · ${plural(decisions, "decision")}`}`;
  return <EvidenceCard title="Autonomy" compact={compact} definitions={calls ? WITH_CALLS : DEFINITIONS} facts={facts(autonomy, compact)} source={source} provenance="Measured: counts and outcomes recorded per episode in this eval">
    <p className="cv-ev__sub">{scope}</p>
    {compact
      ? <dl className="cv-au__kpis cv-au__kpis--compact">
        <Kpi label="Autonomous episodes" value={autonomousValue} sub={autonomousSub} title={EVIDENCE_DEFINITIONS.autonomous} />
        <Kpi label="Interventions per episode" value={one(autonomy.perEpisode)} sub="mean" title={EVIDENCE_DEFINITIONS.intervention} />
        <Kpi label="Per 100 decisions" value={one(autonomy.per100Decisions)} sub="interventions" title={EVIDENCE_DEFINITIONS.decisions} />
      </dl>
      : <dl className="cv-au__kpis">
        <Kpi label="Autonomous episodes" value={autonomousValue} sub={autonomousSub} title={EVIDENCE_DEFINITIONS.autonomous} />
        <Kpi label="Interventions per episode" value={one(autonomy.perEpisode)} sub={interventions === null ? undefined : `${fmtCount(interventions)} in ${plural(episodes, "episode")}`} title={EVIDENCE_DEFINITIONS.intervention} />
        <Kpi label="Per 100 decisions" value={one(autonomy.per100Decisions)} sub={interventions === null || decisions === null ? undefined : `${fmtCount(interventions)} in ${fmtCount(decisions)}`} title={EVIDENCE_DEFINITIONS.decisions} />
        <Kpi label="Decisions between" value={one(autonomy.decisionsBetween)} sub="interventions, mean" title={EVIDENCE_DEFINITIONS.between} missing={noneCounted ? "No interventions" : "Not reported"} />
        <Kpi label="Accepted on first call" value={autonomy.firstCallAccepted === null ? null : `${fmtFixed(autonomy.firstCallAccepted * 100, 0)} %`} sub="of decisions" title={EVIDENCE_DEFINITIONS.firstCall} />
        <Kpi label="Decisions" value={decisions === null ? null : fmtCount(decisions)} sub="valid + failed" title={EVIDENCE_DEFINITIONS.decisions} />
      </dl>}
    {compact ? <BreakdownLine rows={autonomy.breakdown} /> : <Breakdown rows={autonomy.breakdown} />}
    {calls && (compact ? <CallsLine calls={calls} /> : <CallsTable calls={calls} />)}
  </EvidenceCard>;
}
