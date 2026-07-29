"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { StateChip, PolicyChip, fmtTime } from "@/components/bits";
import type { Agent, Approval, Environment, Run, ToolCall } from "@/server/types";

interface RunDetail {
  run: Run;
  agent?: Agent;
  environment?: Environment;
  toolCalls: ToolCall[];
  approvals: Approval[];
}

export default function RunTrace({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<RunDetail | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    const res = await fetch(`/api/runs/${id}`);
    if (res.ok) setData(await res.json());
  }, [id]);

  useEffect(() => {
    void load();
    const es = new EventSource(`/api/stream?runId=${id}`);
    es.onmessage = () => void load();
    return () => es.close();
  }, [id, load]);

  useEffect(() => {
    if (data?.run.state === "running") bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [data?.toolCalls.length, data?.run.state]);

  if (!data) return <div className="muted">Loading trace…</div>;
  const { run, agent, environment, toolCalls, approvals } = data;
  const pending = approvals.filter((a) => a.status === "pending");

  const decide = async (approvalId: string, decision: "approve" | "reject") => {
    setDeciding(approvalId);
    await fetch(`/api/approvals/${approvalId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision }),
    });
    setTimeout(() => {
      setDeciding(null);
      void load();
    }, 300);
  };

  return (
    <div>
      <div className="flex-between" style={{ flexWrap: "wrap", gap: 12 }}>
        <div>
          <h1 className="page-title">{agent?.emoji} {agent?.name} — run trace</h1>
          <div className="flex" style={{ flexWrap: "wrap" }}>
            <StateChip state={run.state} />
            <span className={`pill ${environment?.kind === "production" ? "pill-accent" : "pill-dim"}`}>{environment?.name}</span>
            <span className="pill pill-dim">trigger: {run.triggerType}</span>
            <span className="muted small">started {fmtTime(run.startedAt)}</span>
          </div>
        </div>
        <Link className="btn btn-sm" href={`/agents/${run.agentId}`}>Open agent</Link>
      </div>

      {run.summary && (
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-title">Completion report</div>
          <div style={{ marginTop: 6 }}>{run.summary}</div>
        </div>
      )}

      {pending.map((a) => (
        <div key={a.id} className="approval-card" style={{ marginTop: 16 }}>
          <div className="flex-between">
            <b>✋ Run paused — {a.kind === "agent_flagged" ? "the agent flagged itself" : "the environment forces review"}</b>
            <span className={`pill ${a.kind === "agent_flagged" ? "pill-amber" : "pill-accent"}`}>
              {a.kind === "agent_flagged" ? "agent-flagged" : "policy gate"}
            </span>
          </div>
          <div style={{ marginTop: 8 }}>{a.title}</div>
          <ApprovalDetail approval={a} />
          <div className="approval-actions">
            <button className="btn btn-green" disabled={deciding === a.id} onClick={() => decide(a.id, "approve")}>✓ Approve</button>
            <button className="btn btn-red" disabled={deciding === a.id} onClick={() => decide(a.id, "reject")}>✕ Reject</button>
            <span className="muted small">Approver: {a.approvers.join(", ")} · also notified in Slack</span>
          </div>
        </div>
      ))}

      <div className="section">
        <div className="section-title">Tool calls — inputs, outputs, policy verdicts</div>
        <div className="trace">
          {toolCalls.map((tc) => (
            <div className="trace-row" key={tc.id}>
              <div className="trace-seq">#{tc.seq}</div>
              <div>
                <div className="trace-head">
                  <span className="trace-tool">{tc.tool}</span>
                  <PolicyChip effect={tc.policyEffect} status={tc.status} />
                  {tc.latencyMs > 0 && <span className="muted small">{tc.latencyMs}ms</span>}
                  <span className="muted small">{fmtTime(tc.ts)}</span>
                </div>
                {tc.policyNote && <div className="muted small" style={{ marginTop: 4 }}>policy: {tc.policyNote}</div>}
                <div className="trace-io">
                  <div>
                    <div className="io-label">input</div>
                    <pre>{JSON.stringify(tc.args, null, 2)}</pre>
                  </div>
                  <div>
                    <div className="io-label">output</div>
                    <pre>{tc.result === undefined ? "— awaiting approval —" : JSON.stringify(tc.result, null, 2)}</pre>
                  </div>
                </div>
              </div>
            </div>
          ))}
          {toolCalls.length === 0 && <div className="muted">No tool calls yet…</div>}
          {run.state === "running" && (
            <div className="muted small" style={{ padding: "6px 2px" }}>⏳ agent working…</div>
          )}
        </div>
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function ApprovalDetail({ approval }: { approval: Approval }) {
  const dt = approval.detail as Record<string, unknown>;
  if (approval.kind === "agent_flagged") {
    return (
      <dl className="kv">
        <dt>Reason</dt><dd>{String(dt.reason ?? approval.reason ?? "")}</dd>
        <dt>Proposed action</dt><dd>{String(dt.proposed_action ?? "")}</dd>
      </dl>
    );
  }
  if (approval.tool === "email.send") {
    return (
      <dl className="kv">
        <dt>To</dt><dd className="mono">{String(dt.to ?? "")}</dd>
        <dt>Subject</dt><dd>{String(dt.subject ?? "")}</dd>
        <dt>Body</dt><dd><pre className="doc-view" style={{ padding: 10, fontSize: 12 }}>{String(dt.body ?? "")}</pre></dd>
        {dt.attachmentDocId ? (<><dt>Attachment</dt><dd className="mono">{String(dt.attachmentDocId)} (order form)</dd></>) : null}
      </dl>
    );
  }
  return <pre className="doc-view" style={{ padding: 10, fontSize: 12, marginTop: 8 }}>{JSON.stringify(dt, null, 2)}</pre>;
}
