import Link from "next/link";
import { db } from "@/server/db";
import { TEMPLATES } from "@/server/templates";
import { Badge, EnvBadge, Monogram } from "@/components/bits";

export const dynamic = "force-dynamic";

export default function Agents() {
  const d = db();
  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Agents</h1>
          <p className="page-sub">
            Company agents are workspace assets — versioned, environment-permissioned, and owned by Meridian Labs, not
            by any employee&apos;s login.
          </p>
        </div>
        <div className="page-actions">
          <Link className="btn btn-primary" href="/agents/new">New agent</Link>
        </div>
      </div>

      <div className="section-head">
        <div className="section-title">Deployed agents</div>
      </div>
      <div className="grid grid-2">
        {d.agents.map((agent) => {
          const versions = d.agentVersions.filter((v) => v.agentId === agent.id);
          const deployments = d.deployments.filter((x) => x.agentId === agent.id && x.status === "active");
          return (
            <Link href={`/agents/${agent.id}`} className="card" key={agent.id}>
              <div className="flex-between">
                <div className="card-title"><Monogram name={agent.name} small /> {agent.name}</div>
                {agent.paused ? <Badge tone="danger">Paused</Badge> : <Badge tone="success">Active</Badge>}
              </div>
              <div className="card-sub">{agent.description}</div>
              <div className="flex flex-wrap" style={{ marginTop: 12, gap: 6 }}>
                <span className="tag">v{Math.max(...versions.map((v) => v.version))}</span>
                {deployments.map((dep) => {
                  const env = d.environments.find((e) => e.id === dep.environmentId);
                  return <EnvBadge key={dep.id} kind={env?.kind} name={env?.name} />;
                })}
              </div>
            </Link>
          );
        })}
      </div>

      <div className="section">
        <div className="section-head">
          <div className="section-title">Template catalog</div>
          <div className="section-note">Prebuilt company-agent templates with declared tool requirements</div>
        </div>
        <div className="grid grid-2">
          {TEMPLATES.map((t) => (
            <div className="card" key={t.id}>
              <div className="card-title">{t.name}</div>
              <div className="card-sub">{t.description}</div>
              <div className="flex flex-wrap" style={{ marginTop: 10, gap: 5 }}>
                {t.toolGrants.map((g) => (
                  <span key={g} className="tag">{g}</span>
                ))}
              </div>
              <div style={{ marginTop: 14 }}>
                <Link className="btn btn-sm" href={`/agents/new?template=${t.id}`}>Start from template</Link>
              </div>
            </div>
          ))}
          <div className="card" style={{ borderStyle: "dashed" }}>
            <div className="card-title">From scratch</div>
            <div className="card-sub">
              Name it, write its instructions, grant it tools, set its trigger. The agent is saved as v1, owned by the
              workspace, and bound to Sandbox for validation.
            </div>
            <div style={{ marginTop: 14 }}>
              <Link className="btn btn-sm" href="/agents/new">Create from scratch</Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
