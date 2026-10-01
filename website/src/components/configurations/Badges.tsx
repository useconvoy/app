import type { ReactNode } from "react";
import { RESULT_LABEL, ROLLOUT_LABEL } from "@/lib/configurations/runs";
import type { RolloutResult, RunResult } from "@/lib/configurations/runs";
import { CONFIG_STATUS_LABEL, CONFIG_STATUS_TONE, ROBOT_STATUS_LABEL, ROBOT_STATUS_TONE } from "@/lib/configurations/status";
import type { RobotStatus, Tone } from "@/lib/configurations/status";
import type { ConfigStatus } from "@/lib/configurations/types";

/** A sentence-case status pill. Failed and blocked states use warning, never error red. */
export function Badge({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return <span className={`cv-badge cv-badge--${tone}`} title={title}>{children}</span>;
}

const RESULT_TONE: Record<RunResult, Tone> = { passed: "success", failed: "warning", "below-gate": "warning", running: "info", queued: "neutral", completed: "neutral", cancelled: "neutral", unknown: "neutral" };
/** An eval's result; a running eval with progress reads "Running 24/60". */
export function ResultBadge({ result, progress }: { result: RunResult; progress?: { done: number; total: number } | null }) {
  return <Badge tone={RESULT_TONE[result]}>{RESULT_LABEL[result]}{result === "running" && progress ? ` ${progress.done}/${progress.total}` : ""}</Badge>;
}

const ROLLOUT_TONE: Record<RolloutResult, Tone> = { passed: "success", failed: "warning", timeout: "warning", "safety-stop": "warning", invalid: "warning", running: "info", cancelled: "neutral", unknown: "neutral" };
export function RolloutBadge({ result }: { result: RolloutResult }) {
  return <Badge tone={ROLLOUT_TONE[result]}>{ROLLOUT_LABEL[result]}</Badge>;
}

export function StatusBadge({ status }: { status: RobotStatus }) {
  return <Badge tone={ROBOT_STATUS_TONE[status]}>{status === "online" && <i className="cv-dot" aria-hidden="true" />}{ROBOT_STATUS_LABEL[status]}</Badge>;
}

export function ConfigStatusBadge({ status }: { status: ConfigStatus }) {
  return <Badge tone={CONFIG_STATUS_TONE[status]}>{CONFIG_STATUS_LABEL[status]}</Badge>;
}

/** A small outlined label beside a name, e.g. "Offline sim" on an imported eval. */
export function Tag({ children, title }: { children: ReactNode; title?: string }) {
  return <span className="cv-tag" title={title}>{children}</span>;
}

/** A missing value: an en dash, with the reason for screen readers and on hover. */
export function Missing({ label = "Not reported" }: { label?: string }) {
  return <span className="cv-missing" title={label}><span aria-hidden="true">–</span><span className="cv-sr">{label}</span></span>;
}
