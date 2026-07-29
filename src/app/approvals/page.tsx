"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { timeAgo } from "@/components/bits";
import type { Agent, Approval, Environment } from "@/server/types";

type ApprovalItem = Approval & {
  agent?: Agent;
  environment?: Environment;
  run?: { id: string; triggerType: string; state: string };
};

export default function Approvals() {
  const [items, setItems] = useState<ApprovalItem[]>([]);
  const [deciding, setDeciding] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch("/api/approvals");
    if (res.ok) setItems((await res.json()).approvals);
  }, []);

  useEffect(() => {
    void load();
    const es = new EventSource("/api/stream");
    es.onmessage = (e) => {
      const evt = JSON.parse(e.data);
      if (evt.type === "approval_created" || evt.type === "approval_decided" || evt.type === "run_updated") void load();
    };
    return () => es.close();
  }, [load]);

  const decide = async (id: string, decision: "approve" | "reject") => {
    setDeciding(id);
    await fetch(`/api/approvals/${id}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    setTimeout(() => {
      setDeciding(null);
      void load();
    }, 300);
  };

  const pending = items.filter((a) => a.status === "pending" && a.run?.triggerType !== "test");
  const decided = items.filter((a) => a.status !== "pending").slice(0, 20);

  return (
    <div>
      <h1 className="page-title">Approvals</h1>
      <p className="page-sub">
        One inbox, two paths in: <b>policy-gated</b> (the environment forces review of specific tools, no matter how
        confident the model is) and <b>agent-flagged</b> (the model escalated itself via <span className="mono">flag_for_review</span>).
      </p>

      <div className="section-title">Pending · {pending.length}</div>
      <div className="stack">
        {pending.length === 0 && <div className="card muted">Queue is clear. The fleet is working autonomously within policy.</div>}
        {pending.map((a) => (
          <div className="approval-card" key={a.id}>
            <div className="flex-between" style={{ flexWrap: "wrap", gap: 8 }}>
              <b>{a.agent?.emoji} {a.title}</b>
              <span className="flex">
                <span className={`pill ${a.kind === "agent_flagged" ? "pill-amber" : "pill-accent"}`}>
                  {a.kind === "agent_flagged" ? "🚩 agent-flagged" : "✋ policy gate"}
                </span>
                <span className={`pill ${a.environment?.kind === "production" ? "pill-accent" : "pill-dim"}`}>{a.environment?.name}</span>
              </span>
            </div>
            <Detail a={a} />
            <div className="approval-actions">
              <button className="btn btn-green" disabled={deciding === a.id} onClick={() => decide(a.id, "approve")}>✓ Approve</button>
              <button className="btn btn-red" disabled={deciding === a.id} onClick={() => decide(a.id, "reject")}>✕ Reject</button>
              <Link className="btn" href={`/runs/${a.runId}`}>View trace context →</Link>
              <span className="muted small">requested {timeAgo(a.requestedAt)} · approver: {a.approvers.join(", ")}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="section">
        <div className="section-title">Recently decided</div>
        <div className="card" style={{ padding: 0 }}>
          <table className="table">
            <thead><tr><th>Item</th><th>Kind</th><th>Decision</th><th>Approver</th><th>Latency</th><th>Trace</th></tr></thead>
            <tbody>
              {decided.map((a) => (
                <tr key={a.id}>
                  <td style={{ maxWidth: 400 }}>{a.agent?.emoji} {a.title}</td>
                  <td><span className={`pill ${a.kind === "agent_flagged" ? "pill-amber" : "pill-dim"}`}>{a.kind === "agent_flagged" ? "agent-flagged" : "policy gate"}</span></td>
                  <td><span className={`pill ${a.status === "approved" ? "pill-green" : "pill-red"}`}>{a.status}</span></td>
                  <td className="muted small">{a.approver}</td>
                  <td className="muted small">{a.decidedAt ? `${Math.max(1, Math.round((new Date(a.decidedAt).getTime() - new Date(a.requestedAt).getTime()) / 60000))}m` : "—"}</td>
                  <td><Link href={`/runs/${a.runId}`} className="pill pill-dim">trace →</Link></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function Detail({ a }: { a: ApprovalItem }) {
  const dt = a.detail as Record<string, unknown>;
  if (a.kind === "agent_flagged") {
    return (
      <dl className="kv">
        <dt>Reason</dt><dd>{String(dt.reason ?? a.reason ?? "")}</dd>
        <dt>Proposed action</dt><dd>{String(dt.proposed_action ?? "")}</dd>
      </dl>
    );
  }
  if (a.tool === "email.send") {
    return (
      <dl className="kv">
        <dt>To</dt><dd className="mono">{String(dt.to ?? "")}</dd>
        <dt>Subject</dt><dd>{String(dt.subject ?? "")}</dd>
        <dt>Body</dt><dd><pre className="doc-view" style={{ padding: 10, fontSize: 12 }}>{String(dt.body ?? "")}</pre></dd>
      </dl>
    );
  }
  return <pre className="doc-view" style={{ padding: 10, fontSize: 12, marginTop: 8 }}>{JSON.stringify(dt, null, 2)}</pre>;
}
