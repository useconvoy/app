import Link from "next/link";
import { db } from "@/server/db";
import { StateChip, timeAgo } from "@/components/bits";

export const dynamic = "force-dynamic";

export default function Runs() {
  const d = db();
  const runs = d.runs.slice().sort((a, b) => b.startedAt.localeCompare(a.startedAt)).slice(0, 60);
  return (
    <div>
      <h1 className="page-title">Runs</h1>
      <p className="page-sub">One row per execution of a deployed agent. Every tool call inside is logged and replayable.</p>
      <div className="card" style={{ padding: 0 }}>
        <table className="table">
          <thead><tr><th>Agent</th><th>Environment</th><th>Trigger</th><th>State</th><th>Started</th><th>Summary</th></tr></thead>
          <tbody>
            {runs.map((r) => {
              const agent = d.agents.find((a) => a.id === r.agentId);
              const env = d.environments.find((e) => e.id === r.environmentId);
              return (
                <tr key={r.id}>
                  <td><Link href={`/runs/${r.id}`} style={{ fontWeight: 600 }}>{agent?.emoji} {agent?.name}</Link></td>
                  <td><span className={`pill ${env?.kind === "production" ? "pill-accent" : "pill-dim"}`}>{env?.name}</span></td>
                  <td className="muted">{r.triggerType}</td>
                  <td><StateChip state={r.state} /></td>
                  <td className="muted">{timeAgo(r.startedAt)}</td>
                  <td className="muted small" style={{ maxWidth: 420 }}>{r.summary ?? "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
