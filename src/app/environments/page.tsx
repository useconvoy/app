import { db } from "@/server/db";
import { connectorById } from "@/server/connectors";

export const dynamic = "force-dynamic";

export default function Environments() {
  const d = db();
  return (
    <div>
      <h1 className="page-title">Environments</h1>
      <p className="page-sub">
        Isolated contexts with their own connector instances, credentials, and permission policies. Agents never hold
        credentials — every tool call passes through the policy gateway, which injects environment-scoped credentials
        and applies the rules below.
      </p>
      <div className="grid grid-2">
        {d.environments.map((env) => {
          const instances = d.connectorInstances.filter((ci) => ci.environmentId === env.id);
          const policies = d.policies.filter((p) => p.environmentId === env.id);
          return (
            <div className="card" key={env.id}>
              <div className="flex-between">
                <div className="card-title" style={{ fontSize: 16 }}>
                  {env.kind === "production" ? "🏭" : "🧪"} {env.name}
                </div>
                <span className={`pill ${env.kind === "production" ? "pill-accent" : "pill-dim"}`}>{env.kind}</span>
              </div>

              <div className="label">Connectors</div>
              <div className="stack" style={{ gap: 8 }}>
                {instances.map((ci) => (
                  <div className="flex-between" key={ci.id} style={{ fontSize: 13 }}>
                    <span>
                      <span className={`dot ${ci.health === "green" ? "dot-green" : "dot-red"}`} />{" "}
                      <b>{connectorById(ci.connectorId).name}</b> <span className="muted">· {ci.label}</span>
                    </span>
                    <span className="mono muted">{ci.credentialRef}</span>
                  </div>
                ))}
              </div>

              <div className="label">Permission policy</div>
              <div>
                {policies.map((p) => (
                  <div className="rule" key={p.id}>
                    <span className="mono">{p.tool === "*" ? `${p.connectorId}.*` : p.tool}</span>
                    <span
                      className={`pill ${
                        p.effect === "allow" ? "pill-green" : p.effect === "deny" ? "pill-red" : "pill-amber"
                      }`}
                    >
                      {p.effect === "require_approval" ? "require approval" : p.effect}
                    </span>
                    <span className="muted small">
                      {p.conditions?.recipient_domain_not_in &&
                        `when recipient is outside ${p.conditions.recipient_domain_not_in.join(", ")} · `}
                      {p.approvers && `approver: ${p.approvers.join(", ")} · `}
                      {p.timeoutHours && `timeout ${p.timeoutHours}h → ${p.onTimeout} · `}
                      {p.note}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
