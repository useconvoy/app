"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Badge, EnvBadge, Monogram, StateChip, timeAgo } from "@/components/bits";
import type { Agent, AgentVersion, Deployment, Environment, PromotionDiff, Run, TestRun, TestScenario } from "@/server/types";

interface Detail {
  agent: Agent;
  versions: AgentVersion[];
  latestVersion: AgentVersion;
  deployments: (Deployment & { environment?: Environment })[];
  scenarios: TestScenario[];
  suite: { total: number; passed: number; runs: TestRun[] };
  runs: Run[];
  demoSandboxDeal: { id: string; name: string; stage: string } | null;
}

export default function AgentDetail({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const [data, setData] = useState<Detail | null>(null);
  const [testing, setTesting] = useState(false);
  const [preflight, setPreflight] = useState<(PromotionDiff & { blocked: string | null }) | null>(null);
  const [promoting, setPromoting] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    const res = await fetch(`/api/agents/${id}`);
    if (res.ok) setData(await res.json());
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!data) return <div className="muted">Loading…</div>;
  const { agent, latestVersion, deployments, scenarios, suite, runs } = data;
  const prodDeployment = deployments.find((x) => x.environment?.kind === "production" && x.status === "active");
  const sandboxDeployment = deployments.find((x) => x.environment?.kind === "sandbox" && x.status === "active");
  const suiteGreen = suite.total > 0 && suite.passed === suite.total;

  const runSuite = async () => {
    setTesting(true);
    setNotice(null);
    const res = await fetch("/api/tests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentVersionId: latestVersion.id }),
    });
    const body = await res.json();
    if (!res.ok) setNotice(body.error ?? "Suite failed to start");
    setTesting(false);
    await load();
  };

  const triggerSandboxRun = async () => {
    setNotice(null);
    if (!sandboxDeployment) return;
    if (data.demoSandboxDeal && agent.templateId === "closed_won_paperwork" && data.demoSandboxDeal.stage !== "closedwon") {
      // Marking the sandbox deal Closed-Won fires the CRM webhook (CUJ-3).
      const res = await fetch("/api/webhooks/crm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dealId: data.demoSandboxDeal.id, toStage: "closedwon" }),
      });
      const body = await res.json();
      if (body.runIds?.[0]) router.push(`/runs/${body.runIds[0]}`);
      else setNotice("Webhook fired but no run started — check whether the agent is paused.");
      return;
    }
    const res = await fetch("/api/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deploymentId: sandboxDeployment.id, triggerType: "manual", payload: seedPayload(agent) }),
    });
    const body = await res.json();
    if (res.ok) router.push(`/runs/${body.runId}`);
    else setNotice(body.error ?? "Failed to start run");
  };

  const openPreflight = async () => {
    const res = await fetch(`/api/promotions?versionId=${latestVersion.id}`);
    setPreflight(await res.json());
  };

  const confirmPromotion = async () => {
    setPromoting(true);
    const res = await fetch("/api/promotions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ agentVersionId: latestVersion.id }),
    });
    const body = await res.json();
    setPromoting(false);
    if (!res.ok) {
      setNotice(body.error ?? "Promotion failed");
      setPreflight(null);
      return;
    }
    setPreflight(null);
    await load();
  };

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title flex" style={{ gap: 12 }}>
            <Monogram name={agent.name} /> {agent.name}
          </h1>
          <p className="page-sub">{agent.description}</p>
        </div>
        <div className="page-actions">
          {agent.paused && <Badge tone="danger">Paused fleet-wide</Badge>}
          <span className="tag" style={{ alignSelf: "center" }}>v{latestVersion.version}</span>
          <button className="btn" onClick={triggerSandboxRun} disabled={!sandboxDeployment}>
            Run in Sandbox
          </button>
          <button className="btn btn-primary" onClick={openPreflight}>Promote to Production</button>
        </div>
      </div>

      {notice && (
        <div className="notice notice-danger" style={{ marginBottom: 16 }}>
          <b>{notice}</b>
        </div>
      )}

      <div className="grid grid-2">
        <div className="card">
          <div className="card-title">Deployments</div>
          <div className="stack" style={{ marginTop: 12, gap: 8 }}>
            {deployments.filter((x) => x.status === "active").map((dep) => (
              <div key={dep.id} className="flex-between" style={{ fontSize: 13 }}>
                <EnvBadge kind={dep.environment?.kind} name={dep.environment?.name} />
                <span className="faint small">bound {timeAgo(dep.createdAt)}</span>
              </div>
            ))}
            {!prodDeployment && (
              <div className="faint small">Not in Production yet — promote once the scenario suite is green.</div>
            )}
          </div>
        </div>

        <div className="card">
          <div className="card-title">Configuration — v{latestVersion.version}</div>
          <dl className="kv">
            <dt>Trigger</dt>
            <dd className="mono">
              {latestVersion.trigger.type}
              {latestVersion.trigger.config.to_stage ? ` → ${latestVersion.trigger.config.to_stage}` : ""}
            </dd>
            <dt>Tool grants</dt>
            <dd>
              {latestVersion.toolGrants.map((g) => (
                <span key={g} className="tag" style={{ marginRight: 4, marginBottom: 4 }}>{g}</span>
              ))}
            </dd>
            <dt>Parameters</dt>
            <dd className="mono small">
              {Object.entries(latestVersion.params).map(([k, v]) => (
                <div key={k}>{k} = {v}</div>
              ))}
            </dd>
          </dl>
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <div className="section-title flex" style={{ gap: 10 }}>
            Scenario suite
            <Badge tone={suiteGreen ? "success" : "neutral"}>
              {suite.passed}/{suite.total || scenarios.length} green on v{latestVersion.version}
            </Badge>
          </div>
          <button className="btn btn-primary btn-sm" onClick={runSuite} disabled={testing || scenarios.length === 0}>
            {testing ? "Running against sandbox state…" : "Run scenario suite"}
          </button>
        </div>
        {scenarios.length === 0 ? (
          <div className="empty">No scenario suite exists for this agent yet. A green suite is required before promotion.</div>
        ) : (
          <div className="matrix">
            {scenarios.map((s) => {
              const tr = suite.runs.find((r) => r.scenarioId === s.id);
              const cls = tr ? tr.status : "";
              return (
                <div key={s.id} className={`matrix-cell ${cls}`}>
                  <div className="flex-between">
                    <b style={{ fontSize: 13 }}>{s.name}</b>
                    {tr ? (
                      <Badge tone={tr.status === "pass" ? "success" : tr.status === "fail" ? "danger" : "warning"}>
                        {tr.status === "pass" ? "Pass" : tr.status === "fail" ? "Fail" : "Running"}
                      </Badge>
                    ) : (
                      <Badge tone="neutral">Not run</Badge>
                    )}
                  </div>
                  <div className="faint small" style={{ marginTop: 4 }}>{s.description}</div>
                  {tr && (
                    <details className="small" style={{ marginTop: 8 }}>
                      <summary className="muted">
                        {tr.results.filter((r) => r.pass).length}/{tr.results.length} assertions
                      </summary>
                      {tr.results.map((r, i) => (
                        <div key={i} style={{ marginTop: 5 }} className={r.pass ? "muted" : ""}>
                          <span style={{ color: r.pass ? "var(--success-text)" : "var(--danger-text)", fontWeight: 600 }}>
                            {r.pass ? "Pass" : "Fail"}
                          </span>{" "}
                          <span className="mono">{r.spec.type}</span> — <span className="faint">{r.detail}</span>
                        </div>
                      ))}
                      <div style={{ marginTop: 8 }}>
                        <Link href={`/runs/${tr.runId}`} className="tag">Open run trace</Link>
                      </div>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        )}
        <p className="faint small" style={{ marginTop: 10 }}>
          Every assertion is checked against actual sandbox system state — CRM field values, document existence and
          content, email presence — never against agent self-report.
        </p>
      </div>

      <div className="section">
        <div className="section-head">
          <div className="section-title">Recent runs</div>
        </div>
        <div className="card card-flush">
          <table className="table">
            <thead>
              <tr><th>Run</th><th>Environment</th><th>Trigger</th><th>Status</th><th>Started</th><th>Summary</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td><Link href={`/runs/${r.id}`} className="row-link mono">{r.id.slice(0, 16)}</Link></td>
                  <td className="muted">{deployments.find((x) => x.id === r.deploymentId)?.environment?.name ?? "—"}</td>
                  <td className="muted">{r.triggerType}</td>
                  <td><StateChip state={r.state} /></td>
                  <td className="faint" style={{ whiteSpace: "nowrap" }}>{timeAgo(r.startedAt)}</td>
                  <td className="muted small" style={{ maxWidth: 360 }}>{r.summary ?? "—"}</td>
                </tr>
              ))}
              {runs.length === 0 && (
                <tr><td colSpan={6} className="faint">No runs yet — run the agent in Sandbox to validate it.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {preflight && (
        <div className="modal-backdrop" onClick={() => setPreflight(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>Promote to Production — pre-flight review</h2>
            <p className="muted small" style={{ marginTop: 4 }}>
              Same agent, same version. Different environment, different credentials, different rules.
            </p>

            <div className="label">Credential changes</div>
            {preflight.credentialChanges.map((c) => (
              <div key={c.connector} className="rule">
                <span className="tag">{c.connector}</span>
                <span className="small">{c.from} <span className="faint">→</span> <b>{c.to}</b></span>
              </div>
            ))}

            <div className="label">Policy changes</div>
            {preflight.policyDeltas.length === 0 && (
              <div className="faint small">No policy changes for this agent&apos;s tools.</div>
            )}
            {preflight.policyDeltas.map((p) => (
              <div key={p.tool} className="rule">
                <span className="tag">{p.tool}</span>
                <span className="small">
                  {p.from} <span className="faint">→</span> <b style={{ color: "var(--warning-text)" }}>{p.to}</b>
                </span>
              </div>
            ))}

            <div className="label">Test status</div>
            <Badge tone={preflight.testStatus.passed === preflight.testStatus.total && preflight.testStatus.total > 0 ? "success" : "danger"}>
              {preflight.testStatus.passed}/{preflight.testStatus.total} scenarios green on v{preflight.testStatus.version} — this exact version
            </Badge>

            {preflight.blocked && (
              <div className="notice notice-danger" style={{ marginTop: 14 }}>
                <div><b>Promotion blocked.</b> {preflight.blocked}</div>
              </div>
            )}

            <div className="approval-actions" style={{ marginTop: 18 }}>
              <button className="btn btn-primary" disabled={!!preflight.blocked || promoting} onClick={confirmPromotion}>
                {promoting ? "Promoting…" : "Sign off and promote"}
              </button>
              <button className="btn" onClick={() => setPreflight(null)}>Cancel</button>
              <span className="faint small">Sign-off is recorded in the audit log.</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function seedPayload(agent: Agent): Record<string, unknown> {
  if (agent.templateId === "lead_research") return { leadId: "lead_sbx_1" };
  if (agent.templateId === "docs_sync") return { docTitle: "Pricing & Discount Policy", change: "Discount authority updated" };
  return {};
}
