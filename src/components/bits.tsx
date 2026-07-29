import type { EnvironmentKind, PolicyEffect, RunState, ToolCallStatus } from "@/server/types";

export function Badge({ tone, children }: { tone: "neutral" | "success" | "warning" | "danger" | "info" | "accent"; children: React.ReactNode }) {
  return (
    <span className={`badge${tone === "neutral" ? "" : ` badge-${tone}`}`}>
      <span className="dot" />
      {children}
    </span>
  );
}

export function StateChip({ state }: { state: RunState }) {
  const map: Record<RunState, { tone: "neutral" | "success" | "warning" | "danger" | "info" | "accent"; label: string }> = {
    queued: { tone: "neutral", label: "Queued" },
    running: { tone: "info", label: "Running" },
    paused_pending_approval: { tone: "warning", label: "Awaiting approval" },
    succeeded: { tone: "success", label: "Succeeded" },
    failed: { tone: "danger", label: "Failed" },
    rejected: { tone: "danger", label: "Rejected" },
    killed: { tone: "danger", label: "Stopped" },
  };
  const m = map[state] ?? { tone: "neutral" as const, label: state };
  return <Badge tone={m.tone}>{m.label}</Badge>;
}

export function PolicyChip({ effect, status }: { effect: PolicyEffect | "agent_flagged" | "builtin"; status: ToolCallStatus }) {
  if (effect === "agent_flagged") {
    const suffix = status === "approved_executed" ? " · approved" : status === "rejected" ? " · rejected" : " · pending";
    return <Badge tone="warning">Agent-flagged{suffix}</Badge>;
  }
  if (effect === "require_approval") {
    if (status === "pending_approval") return <Badge tone="warning">Policy gate · pending</Badge>;
    if (status === "approved_executed") return <Badge tone="success">Policy gate · approved</Badge>;
    return <Badge tone="danger">Policy gate · rejected</Badge>;
  }
  if (effect === "deny") return <Badge tone="danger">Denied by policy</Badge>;
  return <Badge tone="success">Allowed</Badge>;
}

export function EnvBadge({ kind, name }: { kind?: EnvironmentKind; name?: string }) {
  if (!name) return <span className="faint">—</span>;
  return <Badge tone={kind === "production" ? "accent" : "neutral"}>{name}</Badge>;
}

/** Two-letter monogram — the enterprise replacement for emoji avatars. */
export function Monogram({ name, small }: { name: string; small?: boolean }) {
  const words = name.replace(/\bAgent\b/gi, "").trim().split(/\s+/).filter(Boolean);
  const initials = ((words[0]?.[0] ?? "") + (words[1]?.[0] ?? words[0]?.[1] ?? "")).toUpperCase();
  return <span className={`monogram${small ? " monogram-sm" : ""}`}>{initials || "AG"}</span>;
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
