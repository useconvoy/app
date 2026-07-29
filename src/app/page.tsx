import Link from "next/link";
import { db } from "@/server/db";
import { PRODUCTION_ID } from "@/server/seed";
import { EnvBadge, Monogram, StateChip, timeAgo } from "@/components/bits";
import { KillSwitch } from "@/components/KillSwitch";

export const dynamic = "force-dynamic";

export default function Dashboard() {
  const d = db();
  const dayAgo = Date.now() - 24 * 3600_000;
  const prodRuns24h = d.runs.filter(
    (r) => r.environmentId === PRODUCTION_ID && new Date(r.startedAt).getTime() > dayAgo
  );
  const pendingApprovals = d.approvals.filter((a) => a.status === "pending");
  const gated24h = d.toolCalls.filter(
    (t) =>
      prodRuns24h.some((r) => r.id === t.runId) &&
      (t.policyEffect === "require_approval" || t.policyEffect === "agent_flagged")
  ).length;
  const calls24h = d.toolCalls.filter((t) => prodRuns24h.some((r) => r.id === t.runId)).length;

  const byAgent = (agentId: string) => prodRuns24h.filter((r) => r.agentId === agentId).length;
  const overnight = {
    leads: byAgent("agent_lead_research"),
    papered: byAgent("agent_closed_won_paperwork"),
    drafts: byAgent("agent_docs_sync"),
    cleaned: byAgent("agent_crm_hygiene"),
  };

  const agents = d.agents.map((agent) => {
    const runs = d.runs.filter((r) => r.agentId === agent.id && r.environmentId === PRODUCTION_ID);
    const finished = runs.filter((r) => ["succeeded", "failed", "rejected", "killed"].includes(r.state));
    const successRate = finished.length
      ? Math.round((finished.filter((r) => r.state === "succeeded").length / finished.length) * 100)
      : null;
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
      <div className="page-head">
        <div>
          <h1 className="page-title">Fleet overview</h1>
          <p className="page-sub">
            Four company agents working the routine layer across the last 24 hours in Production. Every action is
            policy-checked, logged, and attributable; gated actions execute only after recorded human approval.
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn" href="/audit">View audit log</Link>
          <Link className="btn btn-primary" href="/agents">Manage agents</Link>
        </div>
      </div>

      <div className="grid grid-4">
        <div className="card kpi"><div className="kpi-label">Leads researched</div><div className="kpi-value">{overnight.leads}</div><div className="kpi-meta">Lead Research Agent</div></div>
        <div className="card kpi"><div className="kpi-label">Deals papered</div><div className="kpi-value">{overnight.papered}</div><div className="kpi-meta">Closed-Won Paperwork Agent</div></div>
        <div className="card kpi"><div className="kpi-label">Doc drafts produced</div><div className="kpi-value">{overnight.drafts}</div><div className="kpi-meta">Docs Sync Agent</div></div>
        <div className="card kpi"><div className="kpi-label">CRM records cleaned</div><div className="kpi-value">{overnight.cleaned}</div><div className="kpi-meta">CRM Hygiene Agent</div></div>
      </div>

      <div className="section grid grid-4" style={{ gridTemplateColumns: "2fr 1fr 1fr" }}>
        <Link
          href="/approvals"
          className="card kpi"
          style={pendingApprovals.length ? { borderColor: "var(--warning-border)", background: "var(--warning-bg)" } : undefined}
        >
          <div className="kpi-label">Approvals waiting on you</div>
          <div className="kpi-value" style={pendingApprovals.length ? { color: "var(--warning-text)" } : undefined}>
            {pendingApprovals.length}
          </div>
          <div className="kpi-meta">{pendingApprovals.length ? "Runs are paused until reviewed — open the queue" : "Queue is clear"}</div>
        </Link>
        <div className="card kpi">
          <div className="kpi-label">Tool calls (24h)</div>
          <div className="kpi-value">{calls24h}</div>
          <div className="kpi-meta">across production runs</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">Autonomous rate</div>
          <div className="kpi-value">{calls24h ? Math.round(((calls24h - gated24h) / calls24h) * 100) : 100}%</div>
          <div className="kpi-meta">{gated24h} gated on a human</div>
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <div className="section-title">Agents</div>
          <div className="section-note">Production metrics · kill switch pauses an agent fleet-wide</div>
        </div>
        <div className="card card-flush">
          <table className="table">
            <thead>
              <tr>
                <th>Agent</th>
                <th>Status</th>
                <th className="num">Runs</th>
                <th className="num">Success</th>
                <th className="num">Auto / gated</th>
                <th className="num">Approval latency</th>
                <th style={{ width: 220 }}></th>
              </tr>
            </thead>
            <tbody>
              {agents.map(({ agent, runCount, successRate, auto, gated, medianLatency }) => (
                <tr key={agent.id}>
                  <td>
                    <Link href={`/agents/${agent.id}`} className="flex row-link">
                      <Monogram name={agent.name} small /> {agent.name}
                    </Link>
                  </td>
                  <td>
                    {agent.paused ? (
                      <span className="badge badge-danger"><span className="dot" />Paused</span>
                    ) : (
                      <span className="badge badge-success"><span className="dot" />Active</span>
                    )}
                  </td>
                  <td className="num">{runCount}</td>
                  <td className="num">{successRate === null ? "—" : `${successRate}%`}</td>
                  <td className="num">{auto} / {gated}</td>
                  <td className="num">{medianLatency === null ? "—" : `${medianLatency}m median`}</td>
                  <td>
                    <div className="flex" style={{ justifyContent: "flex-end" }}>
                      <Link className="btn btn-sm" href={`/agents/${agent.id}`}>Open</Link>
                      <KillSwitch agentId={agent.id} agentName={agent.name} paused={agent.paused} />
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <div className="section-title">Recent runs</div>
          <Link href="/runs" className="section-note">View all runs</Link>
        </div>
        <div className="card card-flush">
          <table className="table">
            <thead>
              <tr><th>Agent</th><th>Environment</th><th>Trigger</th><th>Status</th><th>Started</th><th>Summary</th></tr>
            </thead>
            <tbody>
              {recentRuns.map((r) => {
                const agent = d.agents.find((a) => a.id === r.agentId);
                const env = d.environments.find((e) => e.id === r.environmentId);
                return (
                  <tr key={r.id}>
                    <td><Link href={`/runs/${r.id}`} className="row-link">{agent?.name}</Link></td>
                    <td><EnvBadge kind={env?.kind} name={env?.name} /></td>
                    <td className="muted">{r.triggerType}</td>
                    <td><StateChip state={r.state} /></td>
                    <td className="faint" style={{ whiteSpace: "nowrap" }}>{timeAgo(r.startedAt)}</td>
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
