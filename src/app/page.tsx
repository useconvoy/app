import Link from "next/link";
import { db } from "@/server/db";
import { PRODUCTION_ID } from "@/server/seed";
import { StateChip, timeAgo } from "@/components/bits";
import { KillSwitch } from "@/components/KillSwitch";

export const dynamic = "force-dynamic";

export default function Dashboard() {
  const d = db();
  const dayAgo = Date.now() - 24 * 3600_000;
  const prodRuns24h = d.runs.filter(
    (r) => r.environmentId === PRODUCTION_ID && new Date(r.startedAt).getTime() > dayAgo
  );
  const pendingApprovals = d.approvals.filter((a) => a.status === "pending");

  const overnight = {
    leads: countSummaries(prodRuns24h, "agent_lead_research"),
    papered: countSummaries(prodRuns24h, "agent_closed_won_paperwork"),
    drafts: countSummaries(prodRuns24h, "agent_docs_sync"),
    cleaned: countSummaries(prodRuns24h, "agent_crm_hygiene"),
  };

  const agents = d.agents.map((agent) => {
    const runs = d.runs.filter((r) => r.agentId === agent.id && r.environmentId === PRODUCTION_ID);
    const finished = runs.filter((r) => ["succeeded", "failed", "rejected", "killed"].includes(r.state));
    const successRate = finished.length ? Math.round((finished.filter((r) => r.state === "succeeded").length / finished.length) * 100) : null;
    const calls = d.toolCalls.filter((t) => runs.some((r) => r.id === t.runId));
    const gated = calls.filter((t) => t.policyEffect === "require_approval" || t.policyEffect === "agent_flagged").length;
    const approvals = d.approvals.filter((a) => a.agentId === agent.id && a.decidedAt);
    const latencies = approvals
      .map((a) => (new Date(a.decidedAt!).getTime() - new Date(a.requestedAt).getTime()) / 60000)
      .sort((a, b) => a - b);
    const medianLatency = latencies.length ? Math.round(latencies[Math.floor(latencies.length / 2)]) : null;
    return { agent, runCount: runs.length, successRate, auto: calls.length - gated, gated, medianLatency };
  });

  const recentRuns = d.runs.slice().sort((a, b) => b.startedAt.localeCompare(a.startedAt)).slice(0, 10);

  return (
    <div>
      <h1 className="page-title">Meridian&apos;s convoy</h1>
      <p className="page-sub">Four company agents working the routine layer. Judgment stays human.</p>

      <div className="banner">
        <div className="stat"><b>{overnight.leads}</b><span>leads researched</span></div>
        <div className="stat"><b>{overnight.papered}</b><span>deals papered</span></div>
        <div className="stat"><b>{overnight.drafts}</b><span>doc drafts</span></div>
        <div className="stat"><b>{overnight.cleaned}</b><span>CRM records cleaned</span></div>
        <div className="stat"><b>0</b><span>humans doing busy work</span></div>
        <div className="lede">Last 24 hours, Production. Every action below was policy-checked, logged, and attributable — gated actions executed only after recorded human approval.</div>
      </div>

      {pendingApprovals.length > 0 && (
        <div className="section">
          <Link href="/approvals" className="card flex-between" style={{ display: "flex", borderColor: "rgba(245,180,83,0.5)" }}>
            <span className="card-title">✋ {pendingApprovals.length} approval{pendingApprovals.length > 1 ? "s" : ""} waiting for you</span>
            <span className="pill pill-amber">review queue →</span>
          </Link>
        </div>
      )}

      <div className="section">
        <div className="section-title">Fleet</div>
        <div className="grid grid-4">
          {agents.map(({ agent, runCount, successRate, auto, gated, medianLatency }) => (
            <div className="card" key={agent.id}>
              <div className="flex-between">
                <div className="card-title">{agent.emoji} {agent.name.replace(" Agent", "")}</div>
                {agent.paused ? <span className="pill pill-red">paused</span> : <span className="pill pill-green">active</span>}
              </div>
              <dl className="kv">
                <dt>Runs (prod)</dt><dd>{runCount}</dd>
                <dt>Success rate</dt><dd>{successRate === null ? "—" : `${successRate}%`}</dd>
                <dt>Auto / gated</dt><dd>{auto} / {gated}</dd>
                <dt>Approval latency</dt><dd>{medianLatency === null ? "—" : `${medianLatency}m median`}</dd>
              </dl>
              <div className="flex" style={{ marginTop: 12 }}>
                <Link className="btn btn-sm" href={`/agents/${agent.id}`}>Open</Link>
                <KillSwitch agentId={agent.id} paused={agent.paused} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="section">
        <div className="section-title">Recent runs</div>
        <div className="card" style={{ padding: 0 }}>
          <table className="table">
            <thead>
              <tr><th>Agent</th><th>Environment</th><th>Trigger</th><th>State</th><th>Started</th><th>Summary</th></tr>
            </thead>
            <tbody>
              {recentRuns.map((r) => {
                const agent = d.agents.find((a) => a.id === r.agentId);
                const env = d.environments.find((e) => e.id === r.environmentId);
                return (
                  <tr key={r.id}>
                    <td><Link href={`/runs/${r.id}`} style={{ fontWeight: 600 }}>{agent?.emoji} {agent?.name}</Link></td>
                    <td><span className={`pill ${env?.kind === "production" ? "pill-accent" : "pill-dim"}`}>{env?.name}</span></td>
                    <td className="muted">{r.triggerType}</td>
                    <td><StateChip state={r.state} /></td>
                    <td className="muted">{timeAgo(r.startedAt)}</td>
                    <td className="muted small" style={{ maxWidth: 380 }}>{r.summary ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function countSummaries(runs: { agentId: string }[], agentId: string): number {
  return runs.filter((r) => r.agentId === agentId).length;
}
