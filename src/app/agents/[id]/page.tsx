"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { StateChip, timeAgo } from "@/components/bits";
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
    if (data.demoSandboxDeal && agent.templateId === "closed_won_paperwork") {
      // Marking the sandbox deal Closed-Won fires the CRM webhook (CUJ-3).
      const res = await fetch("/api/webhooks/crm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dealId: data.demoSandboxDeal.id, toStage: "closedwon" }),
      });
      const body = await res.json();
      if (body.runIds?.[0]) router.push(`/runs/${body.runIds[0]}`);
      else setNotice("Webhook fired but no run started — is the agent paused?");
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
      <div className="flex-between">
        <div>
          <h1 className="page-title">{agent.emoji} {agent.name}</h1>
          <p className="page-sub" style={{ marginBottom: 0 }}>{agent.description}</p>
        </div>
        <div className="flex">
          {agent.paused && <span className="pill pill-red">paused fleet-wide</span>}
          <span className="pill pill-dim">v{latestVersion.version}</span>
        </div>
      </div>

      {notice && (
        <div className="card" style={{ marginTop: 16, borderColor: "rgba(240,106,106,0.5)" }}>
          <b style={{ color: "var(--red)" }}>{notice}</b>
        </div>
      )}

      <div className="section grid grid-2">
        <div className="card">
          <div className="card-title">Deployments</div>
          <div className="stack" style={{ marginTop: 10, gap: 8 }}>
            {deployments.filter((x) => x.status === "active").map((dep) => (
              <div key={dep.id} className="flex-between" style={{ fontSize: 13 }}>
                <span className={`pill ${dep.environment?.kind === "production" ? "pill-accent" : "pill-dim"}`}>{dep.environment?.name}</span>
                <span className="muted small">bound {timeAgo(dep.createdAt)}</span>
              </div>
            ))}
            {!prodDeployment && <div className="muted small">Not yet in Production — promote once the suite is green.</div>}
          </div>
          <div className="flex" style={{ marginTop: 14, flexWrap: "wrap" }}>
            <button className="btn" onClick={triggerSandboxRun} disabled={!sandboxDeployment}>
              ▶ Test-fire in Sandbox
              {agent.templateId === "closed_won_paperwork" && data.demoSandboxDeal ? ` (close '${data.demoSandboxDeal.name.split(" — ")[0]}')` : ""}
            </button>
            <button className="btn btn-primary" onClick={openPreflight}>⬆ Promote to Production…</button>
          </div>
        </div>

        <div className="card">
          <div className="card-title">Configuration (v{latestVersion.version})</div>
          <dl className="kv">
            <dt>Trigger</dt>
            <dd className="mono">{latestVersion.trigger.type}{latestVersion.trigger.config.to_stage ? ` → ${latestVersion.trigger.config.to_stage}` : ""}</dd>
            <dt>Tool grants</dt>
            <dd>{latestVersion.toolGrants.map((g) => <span key={g} className="pill pill-dim mono" style={{ marginRight: 4, marginBottom: 4 }}>{g}</span>)}</dd>
            <dt>Parameters</dt>
            <dd className="mono small">{Object.entries(latestVersion.params).map(([k, v]) => <div key={k}>{k} = {v}</div>)}</dd>
          </dl>
        </div>
      </div>

      <div className="section">
        <div className="flex-between">
          <div className="section-title" style={{ marginBottom: 0 }}>
            Scenario suite · {suite.passed}/{suite.total || scenarios.length} green on v{latestVersion.version}
          </div>
          <button className="btn btn-primary btn-sm" onClick={runSuite} disabled={testing || scenarios.length === 0}>
            {testing ? "Running suite against sandbox state…" : "Run scenario suite"}
          </button>
        </div>
        {scenarios.length === 0 ? (
          <div className="card muted" style={{ marginTop: 12 }}>No scenario suite for this agent yet — suites are required before promotion.</div>
        ) : (
          <div className="matrix" style={{ marginTop: 12 }}>
            {scenarios.map((s) => {
              const tr = suite.runs.find((r) => r.scenarioId === s.id);
              const cls = tr ? tr.status : "";
              return (
                <div key={s.id} className={`matrix-cell ${cls}`}>
                  <div className="flex-between">
                    <b style={{ fontSize: 13 }}>{s.name}</b>
                    {tr ? (
                      <span className={`pill ${tr.status === "pass" ? "pill-green" : tr.status === "fail" ? "pill-red" : "pill-amber"}`}>
                        {tr.status}
                      </span>
                    ) : (
                      <span className="pill pill-dim">not run</span>
                    )}
                  </div>
                  <div className="muted small" style={{ marginTop: 4 }}>{s.description}</div>
                  {tr && (
                    <details className="small" style={{ marginTop: 6 }}>
                      <summary className="muted" style={{ cursor: "pointer" }}>
                        {tr.results.filter((r) => r.pass).length}/{tr.results.length} assertions · trace
                      </summary>
                      {tr.results.map((r, i) => (
                        <div key={i} style={{ marginTop: 4 }}>
                          {r.pass ? "✅" : "❌"} <span className="mono">{r.spec.type}</span> — <span className="muted">{r.detail}</span>
                        </div>
                      ))}
                      <Link href={`/runs/${tr.runId}`} className="pill pill-dim" style={{ marginTop: 6 }}>open run trace →</Link>
                    </details>
                  )}
                </div>
              );
            })}
          </div>
        )}
        <p className="muted small" style={{ marginTop: 10 }}>
          Every assertion is checked against actual sandbox system state — CRM field values, doc existence and content,
          email presence — never against agent self-report.
        </p>
      </div>

      <div className="section">
        <div className="section-title">Recent runs</div>
        <div className="card" style={{ padding: 0 }}>
          <table className="table">
            <thead><tr><th>Run</th><th>Env</th><th>Trigger</th><th>State</th><th>Started</th><th>Summary</th></tr></thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td><Link href={`/runs/${r.id}`} className="mono" style={{ fontWeight: 600 }}>{r.id.slice(0, 14)}…</Link></td>
                  <td className="muted">{deployments.find((x) => x.id === r.deploymentId)?.environment?.name ?? "—"}</td>
                  <td className="muted">{r.triggerType}</td>
                  <td><StateChip state={r.state} /></td>
                  <td className="muted">{timeAgo(r.startedAt)}</td>
                  <td className="muted small" style={{ maxWidth: 360 }}>{r.summary ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {preflight && (
        <div className="modal-backdrop" onClick={() => setPreflight(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2 style={{ fontSize: 17, marginBottom: 4 }}>Promote to Production — pre-flight diff</h2>
            <p className="muted small">Same agent, same version. Different environment, different credentials, different rules.</p>

            <div className="label">Credentials swap</div>
            {preflight.credentialChanges.map((c) => (
              <div key={c.connector} className="rule">
                <span className="mono">{c.connector}</span>
                <span className="small">{c.from} <span className="muted">→</span> <b>{c.to}</b></span>
              </div>
            ))}

            <div className="label">Policy delta</div>
            {preflight.policyDeltas.length === 0 && <div className="muted small">No policy changes for this agent&apos;s tools.</div>}
            {preflight.policyDeltas.map((p) => (
              <div key={p.tool} className="rule">
                <span className="mono">{p.tool}</span>
                <span className="small">{p.from} <span className="muted">→</span> <b style={{ color: "var(--amber)" }}>{p.to}</b></span>
              </div>
            ))}

            <div className="label">Test status</div>
            <div className={`pill ${preflight.testStatus.passed === preflight.testStatus.total && preflight.testStatus.total > 0 ? "pill-green" : "pill-red"}`}>
              {preflight.testStatus.passed}/{preflight.testStatus.total} scenarios green on v{preflight.testStatus.version} — this exact version
            </div>

            {preflight.blocked && (
              <div className="card" style={{ marginTop: 14, borderColor: "rgba(240,106,106,0.5)" }}>
                <b style={{ color: "var(--red)" }}>Blocked:</b> {preflight.blocked}
              </div>
            )}

            <div className="approval-actions" style={{ marginTop: 18 }}>
              <button className="btn btn-green" disabled={!!preflight.blocked || promoting} onClick={confirmPromotion}>
                {promoting ? "Promoting…" : "Sign off & promote"}
              </button>
              <button className="btn" onClick={() => setPreflight(null)}>Cancel</button>
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
