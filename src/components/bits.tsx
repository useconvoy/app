import type { PolicyEffect, RunState, ToolCallStatus } from "@/server/types";

export function StateChip({ state }: { state: RunState }) {
  const map: Record<RunState, { cls: string; label: string }> = {
    queued: { cls: "pill-dim", label: "queued" },
    running: { cls: "pill-accent", label: "running" },
    paused_pending_approval: { cls: "pill-amber", label: "paused — awaiting approval" },
    succeeded: { cls: "pill-green", label: "succeeded" },
    failed: { cls: "pill-red", label: "failed" },
    rejected: { cls: "pill-red", label: "rejected" },
    killed: { cls: "pill-red", label: "killed" },
  };
  const m = map[state] ?? { cls: "pill-dim", label: state };
  return <span className={`pill ${m.cls}`}>{m.label}</span>;
}

export function PolicyChip({ effect, status }: { effect: PolicyEffect | "agent_flagged" | "builtin"; status: ToolCallStatus }) {
  if (effect === "agent_flagged") {
    return <span className="pill pill-amber">🚩 agent-flagged{status === "approved_executed" ? " · approved" : status === "rejected" ? " · rejected" : ""}</span>;
  }
  if (effect === "require_approval") {
    if (status === "pending_approval") return <span className="pill pill-amber">✋ policy gate · pending</span>;
    if (status === "approved_executed") return <span className="pill pill-green">✋ policy gate · approved</span>;
    return <span className="pill pill-red">✋ policy gate · rejected</span>;
  }
  if (effect === "deny") return <span className="pill pill-red">✕ denied by policy</span>;
  return <span className="pill pill-green">✓ allowed</span>;
}

export function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}
