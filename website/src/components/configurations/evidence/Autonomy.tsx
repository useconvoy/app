"use client";

import type { ReactNode } from "react";
import { EVIDENCE_DEFINITIONS, INTERVENTION_LABEL } from "@/lib/configurations/evidence";
import type { Autonomy, InterventionRow } from "@/lib/configurations/evidence";
import { fmtCount, fmtFixed } from "@/lib/configurations/format";
import { Missing, Tag } from "../Badges";
import { EvidenceCard, type Fact } from "./EvidenceCard";

const one = (value: number | null) => value === null ? null : fmtFixed(value, 1);
const plural = (n: number, word: string) => `${fmtCount(n)} ${word}${n === 1 ? "" : "s"}`;

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
 * first-call acceptance, and the interventions by type. `compact`: the dashboard's version, with
 * `source` (a link to the eval) in its foot.
 */
export function AutonomyPanel({ autonomy, source, compact = false }: { autonomy: Autonomy; source?: ReactNode; compact?: boolean }) {
  const { episodes, autonomous, autonomousShare, interventions, decisions } = autonomy;
  const autonomousValue = autonomous === null ? null : `${fmtCount(autonomous)} / ${fmtCount(episodes)}`;
  const autonomousSub = autonomousShare === null ? undefined : `${fmtFixed(autonomousShare * 100, 0)} %`;
  const noneCounted = interventions === 0 && decisions !== null;
  const scope = `Teleop handoffs · ${plural(episodes, "episode")}${decisions === null ? "" : ` · ${plural(decisions, "decision")}`}`;
  return <EvidenceCard title="Autonomy" compact={compact} definitions={DEFINITIONS} facts={facts(autonomy, compact)} source={source} provenance="Measured: counts and outcomes recorded per episode in this eval">
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
  </EvidenceCard>;
}
