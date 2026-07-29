import Link from "next/link";
import { db } from "@/server/db";
import { fmtTime } from "@/components/bits";

export const dynamic = "force-dynamic";

export default async function Audit({ searchParams }: { searchParams: Promise<{ q?: string; runId?: string }> }) {
  const { q, runId } = await searchParams;
  const d = db();
  let events = d.auditEvents.slice().sort((a, b) => b.ts.localeCompare(a.ts));
  if (runId) events = events.filter((e) => e.refs.runId === runId);
  if (q) {
    const needle = q.toLowerCase();
    events = events.filter((e) => e.message.toLowerCase().includes(needle) || e.type.includes(needle) || e.actor.toLowerCase().includes(needle));
  }

  return (
    <div>
      <h1 className="page-title">Audit log</h1>
      <p className="page-sub">
        Append-only. Any production action traces to its full causal chain — trigger, every read, every write, policy
        verdicts, approver identity — in two clicks: find the event, open its run trace.
      </p>

      <form className="flex" style={{ marginBottom: 16 }}>
        <input className="input" style={{ maxWidth: 420 }} name="q" defaultValue={q ?? ""} placeholder='Filter — try "Invoice", "approved", "kill switch"…' />
        <button className="btn" type="submit">Filter</button>
        {(q || runId) && <Link className="btn" href="/audit">Clear</Link>}
      </form>

      <div className="card" style={{ padding: 0 }}>
        <table className="table">
          <thead><tr><th>When</th><th>Actor</th><th>Event</th><th>Detail</th><th>Causal chain</th></tr></thead>
          <tbody>
            {events.slice(0, 120).map((e) => (
              <tr key={e.id}>
                <td className="muted mono small" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.ts)}</td>
                <td className="small" style={{ whiteSpace: "nowrap" }}>
                  {e.actor.startsWith("agent:") ? (
                    <span className="pill pill-accent">{d.agents.find((a) => a.id === e.actor.slice(6))?.name ?? "agent"}</span>
                  ) : (
                    e.actor
                  )}
                </td>
                <td><span className="mono small">{e.type}</span></td>
                <td className="small" style={{ maxWidth: 460 }}>{e.message}</td>
                <td>
                  {e.refs.runId && <Link href={`/runs/${e.refs.runId}`} className="pill pill-dim">run trace →</Link>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted small" style={{ marginTop: 10 }}>{events.length} event(s){q ? ` matching "${q}"` : ""}.</p>
    </div>
  );
}
