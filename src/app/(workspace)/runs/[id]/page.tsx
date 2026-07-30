"use client";

import { use, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { Badge, EnvBadge, PolicyChip, StateChip, fmtTime } from "@/components/bits";
import type { Agent, Approval, CloudMission, Environment, Run, ToolCall } from "@/server/types";

interface RunDetail {
  run: Run;
  agent?: Agent;
  environment?: Environment;
  toolCalls: ToolCall[];
  approvals: Approval[];
  cloudMission?: CloudMission;
}

export default function RunTrace({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deciding, setDeciding] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/runs/${id}`);
      if (!res.ok) throw new Error(`Request failed with status ${res.status}`);
      setData(await res.json());
      setError(null);
    } catch {
      setError("This trace could not be loaded. Check your connection and try again.");
    }
  }, [id]);

  useEffect(() => {
    void load();
    const es = new EventSource(`/api/stream?runId=${id}`);
    es.onmessage = () => void load();
    const poll = window.setInterval(() => void load(), 1500);
    return () => {
      es.close();
      window.clearInterval(poll);
    };
  }, [id, load]);

  useEffect(() => {
    if (data?.run.state === "running") {
      const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      bottomRef.current?.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "nearest" });
    }
  }, [data?.toolCalls.length, data?.run.state]);

  if (error && !data) {
    return (
      <div className="error-state" role="alert">
        <strong>Run trace unavailable.</strong>
        <span>{error}</span>
        <button className="btn btn-sm" type="button" onClick={() => void load()}>Try again</button>
      </div>
    );
  }
  if (!data) return <div className="muted" role="status">Loading trace…</div>;
  const { run, agent, environment, toolCalls, approvals, cloudMission } = data;
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
      <div className="page-head">
        <div>
          <h1 className="page-title">{agent?.name} — run trace</h1>
          <div className="flex flex-wrap" style={{ marginTop: 8 }}>
            <StateChip state={run.state} />
            <EnvBadge kind={environment?.kind} name={environment?.name} />
            <Badge tone="neutral">Trigger: {run.triggerType}</Badge>
            <span className="faint small">Started {fmtTime(run.startedAt)}</span>
          </div>
        </div>
        <div className="page-actions">
          <Link className="btn btn-sm" href={`/agents/${run.agentId}`}>Open agent</Link>
        </div>
      </div>

      {run.summary && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-title">Completion report</div>
          <div className="muted" style={{ marginTop: 6, fontSize: 13.5 }}>{run.summary}</div>
        </div>
      )}

      {cloudMission && (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="flex-between">
            <div>
              <div className="card-title">Elastic agent compute pool</div>
              <div className="faint small" style={{ marginTop: 4 }}>
                Temporal coordinates the mission; each row is an isolated AWS Fargate agent episode.
              </div>
            </div>
            <Badge tone={cloudMission.status === "COMPLETED" ? "success" : "accent"}>
              {cloudMission.agents.filter((item) => item.status === "COMPLETED").length}
              /{cloudMission.agents.length || 1} complete
            </Badge>
          </div>
          <div className="trace" style={{ marginTop: 14 }}>
            {cloudMission.agents.map((item) => {
              let artifact: { finding?: string } | undefined;
              try {
                artifact = item.artifact ? JSON.parse(item.artifact) : undefined;
              } catch {
                artifact = undefined;
              }
              return (
                <div
                  className="trace-row"
                  key={item.agentId}
                  style={{ marginLeft: item.depth * 22 }}
                >
                  <div className="trace-seq">{item.agentId}</div>
                  <div>
                    <div className="trace-head">
                      <span className="trace-tool">Agent Episode</span>
                      <Badge tone={item.status === "COMPLETED" ? "success" : "warning"}>
                        {item.status}
                      </Badge>
                      <span className="faint small">{item.computeProvider ?? "AWS Fargate"}</span>
                    </div>
                    {artifact?.finding && (
                      <div className="muted small" style={{ marginTop: 5 }}>
                        {artifact.finding}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            {cloudMission.agents.length === 0 && (
              <div className="empty">Temporal is admitting the root agent…</div>
            )}
          </div>
        </div>
      )}

      {pending.map((a) => (
        <div key={a.id} className="approval-card" style={{ marginBottom: 16 }}>
          <div className="flex-between">
            <b>Run paused — {a.kind === "agent_flagged" ? "the agent flagged itself for review" : "the environment requires review"}</b>
            <Badge tone={a.kind === "agent_flagged" ? "warning" : "accent"}>
              {a.kind === "agent_flagged" ? "Agent-flagged" : "Policy gate"}
            </Badge>
          </div>
          <div style={{ marginTop: 8, fontSize: 13.5 }}>{a.title}</div>
          <ApprovalDetail approval={a} />
          <div className="approval-actions">
            <button className="btn btn-success" disabled={deciding === a.id} onClick={() => decide(a.id, "approve")}>Approve</button>
            <button className="btn btn-danger" disabled={deciding === a.id} onClick={() => decide(a.id, "reject")}>Reject</button>
            <span className="faint small">Approver: {a.approvers.join(", ")} · also notified in Slack</span>
          </div>
        </div>
      ))}

      <div className="section" style={{ marginTop: 20 }}>
        <div className="section-head">
          <div className="section-title">Tool calls</div>
          <div className="section-note">Inputs, outputs, and policy verdicts — recorded causal trace</div>
        </div>
        <div className="trace">
          {toolCalls.map((tc) => (
            <div className="trace-row" key={tc.id}>
              <div className="trace-seq">#{tc.seq}</div>
              <div>
                <div className="trace-head">
                  <span className="trace-tool">{tc.tool}</span>
                  <PolicyChip effect={tc.policyEffect} status={tc.status} />
                  {tc.latencyMs > 0 && <span className="faint small num">{tc.latencyMs} ms</span>}
                  <span className="faint small">{fmtTime(tc.ts)}</span>
                </div>
                {tc.policyNote && <div className="faint small" style={{ marginTop: 4 }}>Policy: {tc.policyNote}</div>}
                <div className="trace-io">
                  <div>
                    <div className="io-label">Input</div>
                    <pre>{JSON.stringify(tc.args, null, 2)}</pre>
                  </div>
                  <div>
                    <div className="io-label">Output</div>
                    <pre>{tc.result === undefined ? "— awaiting approval —" : JSON.stringify(tc.result, null, 2)}</pre>
                  </div>
                </div>
              </div>
            </div>
          ))}
          {toolCalls.length === 0 && <div className="empty">No tool calls yet.</div>}
          {run.state === "running" && <div className="faint small" style={{ padding: "6px 2px" }}>Agent working…</div>}
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
        <dt>Body</dt><dd><div className="doc-view" style={{ padding: 10, fontSize: 12 }}>{String(dt.body ?? "")}</div></dd>
        {dt.attachmentDocId ? (<><dt>Attachment</dt><dd className="mono">{String(dt.attachmentDocId)} (order form)</dd></>) : null}
      </dl>
    );
  }
  return <pre className="doc-view" style={{ padding: 10, fontSize: 12, marginTop: 8 }}>{JSON.stringify(dt, null, 2)}</pre>;
}
