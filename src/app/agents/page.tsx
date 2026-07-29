import Link from "next/link";
import { db } from "@/server/db";
import { TEMPLATES } from "@/server/templates";

export const dynamic = "force-dynamic";

export default function Agents() {
  const d = db();
  return (
    <div>
      <h1 className="page-title">Agents</h1>
      <p className="page-sub">
        Company agents are workspace assets — versioned, environment-permissioned, and owned by Meridian, not by anyone&apos;s
        login.
      </p>

      <div className="section-title">Your agents</div>
      <div className="grid grid-2">
        {d.agents.map((agent) => {
          const versions = d.agentVersions.filter((v) => v.agentId === agent.id);
          const deployments = d.deployments.filter((x) => x.agentId === agent.id && x.status === "active");
          return (
            <Link href={`/agents/${agent.id}`} className="card" key={agent.id}>
              <div className="flex-between">
                <div className="card-title">{agent.emoji} {agent.name}</div>
                {agent.paused ? <span className="pill pill-red">paused</span> : <span className="pill pill-green">active</span>}
              </div>
              <div className="card-sub">{agent.description}</div>
              <div className="flex" style={{ marginTop: 12, flexWrap: "wrap" }}>
                <span className="pill pill-dim">v{Math.max(...versions.map((v) => v.version))}</span>
                {deployments.map((dep) => {
                  const env = d.environments.find((e) => e.id === dep.environmentId);
                  return (
                    <span key={dep.id} className={`pill ${env?.kind === "production" ? "pill-accent" : "pill-dim"}`}>
                      {env?.name}
                    </span>
                  );
                })}
              </div>
            </Link>
          );
        })}
      </div>

      <div className="section">
        <div className="section-title">Hire a new agent — template catalog</div>
        <div className="grid grid-2">
          {TEMPLATES.map((t) => (
            <div className="card" key={t.id}>
              <div className="card-title">{t.emoji} {t.name}</div>
              <div className="card-sub">{t.description}</div>
              <div className="flex" style={{ marginTop: 10, flexWrap: "wrap", gap: 6 }}>
                {t.toolGrants.map((g) => (
                  <span key={g} className="pill pill-dim mono">{g}</span>
                ))}
              </div>
              <div style={{ marginTop: 14 }}>
                <Link className="btn btn-sm btn-primary" href={`/agents/new?template=${t.id}`}>Start from template</Link>
              </div>
            </div>
          ))}
          <div className="card" style={{ borderStyle: "dashed" }}>
            <div className="card-title">✨ From scratch</div>
            <div className="card-sub">Name it, write its instructions, grant it tools, set its trigger. Ninety seconds to hire a digital worker.</div>
            <div style={{ marginTop: 14 }}>
              <Link className="btn btn-sm" href="/agents/new">Create from scratch</Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
