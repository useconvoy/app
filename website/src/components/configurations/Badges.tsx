import type { ReactNode } from "react";
import { provenanceDetail, provenanceLabel } from "@/lib/configurations/format";
import type { ConfigStatus as ConfigStatusValue, Configuration, EvalRun, Provenance, RobotHealth, RobotRole, RolloutOutcome, RunStatus as RunStatusValue, SpanPath, TraceOutcome } from "@/lib/configurations/types";
import { Icon, type IconName } from "./Icons";
import { ProgressBar } from "./ProgressBar";

/** Badge tones (failed and blocked states use warning, never error red). */
export type BadgeTone = "neutral" | "success" | "warning" | "info" | "forest" | "error";

/** `cfg-badge`: sentence-case mono pill. */
export function Badge({ tone = "neutral", icon, children, title }: { tone?: BadgeTone; icon?: IconName; children: ReactNode; title?: string }) {
  return <span className={`cfg-badge${tone === "neutral" ? "" : ` cfg-badge--${tone}`}`} title={title}>{icon && <Icon name={icon} />}{children}</span>;
}
/** A wrapping row of badges. */
export function Badges({ children }: { children: ReactNode }) { return <span className="cfg-badges">{children}</span>; }
/** Mono revision label, e.g. "r4". */
export function Version({ children }: { children: ReactNode }) { return <span className="cfg-ver">{children}</span>; }

const PROVENANCE_CLASS = { measured: "measured", recorded: "recorded", sample: "sample", "not-reported": "none" } as const;
/**
 * Exactly one per metric: "Measured · 9s ago" | "Recorded · Sep 14" | "Sample" | "Not reported".
 * `now` (wall-clock ms, from `useNow()`) adds the age to measured values; `label` overrides the text
 * (e.g. "Not measured · no cloud endpoint"); the title carries date, n and source.
 */
export function ProvenanceBadge({ provenance, now, label }: { provenance: Provenance | null | undefined; now?: number | null; label?: string }) {
  const kind = provenance?.kind ?? "not-reported";
  return <span className={`cfg-prov cfg-prov--${PROVENANCE_CLASS[kind]}`} title={provenance ? provenanceDetail(provenance) : undefined}>{label ?? provenanceLabel(provenance, now)}</span>;
}
/** "Not reported" in place of a missing value (never 0 or a dash). */
export function NotReported({ children = "Not reported" }: { children?: ReactNode }) { return <span className="cfg-prov cfg-prov--none">{children}</span>; }

export const ROLE_LABEL: Record<RobotRole, string> = { test: "Test", production: "Production" };
export function RoleBadge({ role }: { role: RobotRole }) { return <Badge tone={role === "test" ? "info" : "forest"}>{ROLE_LABEL[role]}</Badge>; }

export const HEALTH_LABEL: Record<RobotHealth, string> = { healthy: "Healthy", degraded: "Degraded", attention: "Needs attention", offline: "Offline", "not-reported": "Not reported" };
/** Healthy / Degraded / Needs attention (with flag) / Offline / Not reported. */
export function HealthBadge({ health }: { health: RobotHealth }) {
  if (health === "not-reported") return <NotReported />;
  return <Badge tone={health === "healthy" ? "success" : health === "offline" ? "neutral" : "warning"} icon={health === "attention" ? "flag" : undefined}>{HEALTH_LABEL[health]}</Badge>;
}

export const CONFIG_STATUS_LABEL: Record<ConfigStatusValue, string> = { draft: "Draft", testing: "Testing", production: "In production" };
/** One status badge: "Draft", "Testing r4" or "In production r3". */
export function ConfigStatus({ status, rev }: { status: ConfigStatusValue; rev?: string | null }) {
  return <Badge tone={status === "draft" ? "neutral" : status === "testing" ? "info" : "forest"}>{CONFIG_STATUS_LABEL[status]}{rev ? ` ${rev}` : ""}</Badge>;
}
/** A configuration's badges: "In production r3" and "Testing r4", or "Draft r1". */
export function ConfigStatusBadges({ configuration }: { configuration: Pick<Configuration, "status" | "productionRev" | "candidateRev"> }) {
  const { status, productionRev, candidateRev } = configuration;
  if (status === "draft") return <ConfigStatus status="draft" rev={candidateRev} />;
  return <>{productionRev && <ConfigStatus status="production" rev={productionRev} />}{candidateRev && <ConfigStatus status="testing" rev={candidateRev} />}</>;
}

export const RUN_STATUS_LABEL: Record<RunStatusValue, string> = { queued: "Queued", running: "Running", "passed-gate": "Passed gate", "below-gate": "Below gate", completed: "Completed", "did-not-qualify": "Did not qualify", cancelled: "Cancelled", failed: "Failed" };
const RUN_TONE: Record<RunStatusValue, BadgeTone> = { queued: "neutral", running: "info", "passed-gate": "success", "below-gate": "warning", completed: "neutral", "did-not-qualify": "warning", cancelled: "neutral", failed: "warning" };
/** Run state; a running run shows "Running 212/360" with its progress bar. */
export function RunStatus({ run }: { run: Pick<EvalRun, "status" | "progress" | "number"> }) {
  if (run.status === "running" && run.progress) {
    return <span className="cfg-run"><Badge tone="info">Running {run.progress.done.toLocaleString("en-US")}/{run.progress.total.toLocaleString("en-US")}</Badge><ProgressBar done={run.progress.done} total={run.progress.total} label={`Run ${run.number} episodes complete`} /></span>;
  }
  return <Badge tone={RUN_TONE[run.status]}>{RUN_STATUS_LABEL[run.status]}</Badge>;
}

export const OUTCOME_LABEL: Record<RolloutOutcome | TraceOutcome, string> = { succeeded: "Succeeded", failed: "Failed", timeout: "Timeout", "safety-stop": "Safety stop", escalated: "Escalated · resolved", clarified: "Clarified" };
/** Rollout or action outcome. */
export function OutcomeBadge({ outcome, label }: { outcome: RolloutOutcome | TraceOutcome; label?: string }) {
  return <Badge tone={outcome === "succeeded" ? "success" : outcome === "clarified" ? "neutral" : "warning"}>{label ?? OUTCOME_LABEL[outcome]}</Badge>;
}

export const PATH_LABEL: Record<SpanPath, string> = { edge: "Edge", cloud: "Cloud", fallback: "Fallback", operator: "Operator", none: "Other" };
/** Edge / Cloud / Fallback / Operator chip: ink label plus a series square, never colour alone. */
export function PathChip({ path }: { path: SpanPath }) {
  return <span className={`cfg-path${path === "none" ? "" : ` cfg-path--${path}`}`}>{PATH_LABEL[path]}</span>;
}
/** Mono tags, e.g. scenario slices on a rollout. */
export function Tags({ items }: { items: ReadonlyArray<string> }) {
  return <span className="cfg-tags">{items.map(item => <span className="cfg-tag" key={item}>{item}</span>)}</span>;
}
