"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Badge, EnvBadge, timeAgo } from "@/components/bits";
import type { Agent, Approval, Environment } from "@/server/types";

type ApprovalItem = Approval & {
  agent?: Agent;
  environment?: Environment;
  run?: { id: string; triggerType: string; state: string };
};

export default function Approvals() {
  const [items, setItems] = useState<ApprovalItem[] | null>(null);
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

  const pending = (items ?? []).filter((a) => a.status === "pending" && a.run?.triggerType !== "test");
  const decided = (items ?? []).filter((a) => a.status !== "pending").slice(0, 20);

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Approvals</h1>
          <p className="page-sub">
            One inbox, two paths in: <b>policy-gated</b> — the environment forces review of specific tools regardless of
            model confidence — and <b>agent-flagged</b> — the model escalated itself via{" "}
            <span className="mono">flag_for_review</span>. Approving executes the action and resumes the run; rejecting
            terminates the run cleanly with the decision on the trace.
          </p>
        </div>
      </div>

      <div className="section-head">
        <div className="section-title">Pending ({pending.length})</div>
      </div>
      <div className="stack">
        {items === null ? (
          <div className="faint">Loading…</div>
        ) : pending.length === 0 ? (
          <div className="empty">The queue is clear. The fleet is working autonomously within policy.</div>
        ) : (
          pending.map((a) => (
            <div className="approval-card" key={a.id}>
              <div className="flex-between flex-wrap">
                <b style={{ fontSize: 13.5 }}>{a.title}</b>
                <span className="flex">
                  <Badge tone={a.kind === "agent_flagged" ? "warning" : "accent"}>
                    {a.kind === "agent_flagged" ? "Agent-flagged" : "Policy gate"}
                  </Badge>
                  <EnvBadge kind={a.environment?.kind} name={a.environment?.name} />
                </span>
              </div>
              <Detail a={a} />
              <div className="approval-actions">
                <button className="btn btn-success" disabled={deciding === a.id} onClick={() => decide(a.id, "approve")}>Approve</button>
                <button className="btn btn-danger" disabled={deciding === a.id} onClick={() => decide(a.id, "reject")}>Reject</button>
                <Link className="btn" href={`/runs/${a.runId}`}>View trace context</Link>
                <span className="faint small">Requested {timeAgo(a.requestedAt)} · approver: {a.approvers.join(", ")}</span>
              </div>
            </div>
          ))
        )}
      </div>

      <div className="section">
        <div className="section-head">
          <div className="section-title">Recently decided</div>
        </div>
        <div className="card card-flush">
          <table className="table">
            <thead>
              <tr><th>Item</th><th>Kind</th><th>Decision</th><th>Approver</th><th className="num">Latency</th><th>Trace</th></tr>
            </thead>
            <tbody>
              {decided.map((a) => (
                <tr key={a.id}>
                  <td style={{ maxWidth: 420 }}>{a.title}</td>
                  <td>
                    <Badge tone={a.kind === "agent_flagged" ? "warning" : "neutral"}>
                      {a.kind === "agent_flagged" ? "Agent-flagged" : "Policy gate"}
                    </Badge>
                  </td>
                  <td>
                    <Badge tone={a.status === "approved" ? "success" : "danger"}>
                      {a.status === "approved" ? "Approved" : "Rejected"}
                    </Badge>
                  </td>
                  <td className="muted small">{a.approver}</td>
                  <td className="num muted small">
                    {a.decidedAt ? `${Math.max(1, Math.round((new Date(a.decidedAt).getTime() - new Date(a.requestedAt).getTime()) / 60000))}m` : "—"}
                  </td>
                  <td><Link href={`/runs/${a.runId}`} className="tag">Open trace</Link></td>
                </tr>
              ))}
              {decided.length === 0 && <tr><td colSpan={6} className="faint">Nothing decided yet.</td></tr>}
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
        <dt>Body</dt><dd><div className="doc-view" style={{ padding: 10, fontSize: 12 }}>{String(dt.body ?? "")}</div></dd>
      </dl>
    );
  }
  return <pre className="doc-view" style={{ padding: 10, fontSize: 12, marginTop: 8 }}>{JSON.stringify(dt, null, 2)}</pre>;
}
