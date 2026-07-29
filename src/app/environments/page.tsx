import { db } from "@/server/db";
import { connectorById } from "@/server/connectors";
import { Badge } from "@/components/bits";

export const dynamic = "force-dynamic";

export default function Environments() {
  const d = db();
  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Environments</h1>
          <p className="page-sub">
            Isolated contexts with their own connector instances, credentials, and permission policies. Agents never
            hold credentials — every tool call passes through the policy gateway, which injects environment-scoped
            credentials and applies the rules below.
          </p>
        </div>
      </div>
      <div className="grid grid-2">
        {d.environments.map((env) => {
          const instances = d.connectorInstances.filter((ci) => ci.environmentId === env.id);
          const policies = d.policies.filter((p) => p.environmentId === env.id);
          return (
            <div className="card" key={env.id}>
              <div className="flex-between">
                <div className="card-title" style={{ fontSize: 15 }}>{env.name}</div>
                <Badge tone={env.kind === "production" ? "accent" : "neutral"}>{env.kind}</Badge>
              </div>

              <div className="label">Connectors</div>
              <div className="stack" style={{ gap: 8 }}>
                {instances.map((ci) => (
                  <div className="flex-between" key={ci.id} style={{ fontSize: 13 }}>
                    <span className="flex" style={{ gap: 8 }}>
                      <span
                        className="dot"
                        style={{
                          width: 8, height: 8, borderRadius: "50%", display: "inline-block",
                          background: ci.health === "green" ? "var(--success-dot)" : "var(--danger-dot)",
                        }}
                        aria-label={ci.health === "green" ? "healthy" : "unreachable"}
                      />
                      <b>{connectorById(ci.connectorId).name}</b>
                      <span className="muted">{ci.label}</span>
                    </span>
                    <span className="tag">{ci.credentialRef}</span>
                  </div>
                ))}
              </div>

              <div className="label">Permission policy</div>
              <div>
                {policies.map((p) => (
                  <div className="rule" key={p.id}>
                    <span className="tag">{p.tool === "*" ? `${p.connectorId}.*` : p.tool}</span>
                    <Badge tone={p.effect === "allow" ? "success" : p.effect === "deny" ? "danger" : "warning"}>
                      {p.effect === "require_approval" ? "Require approval" : p.effect === "allow" ? "Allow" : "Deny"}
                    </Badge>
                    <span className="faint small">
                      {p.conditions?.recipient_domain_not_in &&
                        `when recipient is outside ${p.conditions.recipient_domain_not_in.join(", ")} · `}
                      {p.approvers && `approver: ${p.approvers.join(", ")} · `}
                      {p.timeoutHours && `timeout ${p.timeoutHours}h then ${p.onTimeout} · `}
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
