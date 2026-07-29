import { db, persist } from "./db";
import { id, now } from "./ids";
import { latestSuiteStatus } from "./testing/harness";
import { connectorForTool } from "./connectors";
import { evaluatePolicy } from "./gateway";
import { PRODUCTION_ID, SANDBOX_ID } from "./seed";
import type { Deployment, Promotion, PromotionDiff } from "./types";

// Promotion (spec CUJ-4): pre-flight diff of credentials + policy + test
// status, blocked unless the suite is green on this exact version.

export function preflight(agentVersionId: string): PromotionDiff & { blocked: string | null } {
  const d = db();
  const version = d.agentVersions.find((v) => v.id === agentVersionId);
  if (!version) throw new Error("Unknown agent version");

  const connectors = new Set(
    version.toolGrants.map((t) => connectorForTool(t)?.id).filter(Boolean) as string[]
  );
  const credentialChanges = [...connectors].map((cid) => {
    const sbx = d.connectorInstances.find((ci) => ci.environmentId === SANDBOX_ID && ci.connectorId === cid);
    const prod = d.connectorInstances.find((ci) => ci.environmentId === PRODUCTION_ID && ci.connectorId === cid);
    return { connector: cid, from: sbx?.label ?? "(not attached)", to: prod?.label ?? "(not attached)" };
  });

  const policyDeltas: PromotionDiff["policyDeltas"] = [];
  for (const tool of version.toolGrants) {
    const connector = connectorForTool(tool);
    if (!connector) continue;
    const sbx = describePolicy(SANDBOX_ID, connector.id, tool);
    const prod = describePolicy(PRODUCTION_ID, connector.id, tool);
    if (sbx !== prod) policyDeltas.push({ tool, from: sbx, to: prod });
  }

  const suite = latestSuiteStatus(agentVersionId);
  const testStatus = { total: suite.total, passed: suite.passed, version: version.version };

  let blocked: string | null = null;
  if (suite.total === 0) blocked = "No scenario suite exists for this agent — a green suite is required to promote.";
  else if (suite.passed < suite.total) blocked = `Suite is not green on this version (${suite.passed}/${suite.total}). Run it in Sandbox first.`;
  const missing = credentialChanges.filter((c) => c.to === "(not attached)");
  if (!blocked && missing.length) blocked = `Production is missing connector(s): ${missing.map((m) => m.connector).join(", ")}`;

  return { credentialChanges, policyDeltas, testStatus, blocked };
}

function describePolicy(envId: string, connectorId: string, tool: string): string {
  const d = db();
  const rules = d.policies.filter((p) => p.environmentId === envId && p.connectorId === connectorId);
  const rule = rules.find((p) => p.tool === tool) ?? rules.find((p) => p.tool === "*");
  const env = d.environments.find((e) => e.id === envId)!;
  if (!rule) return env.kind === "sandbox" ? "auto-allow (default)" : "requires approval (default)";
  if (rule.effect === "require_approval") {
    const cond = rule.conditions?.recipient_domain_not_in ? ` for recipients outside ${rule.conditions.recipient_domain_not_in.join(", ")}` : "";
    return `requires approval by ${rule.approvers?.join(", ") ?? "revops_lead"}${cond}`;
  }
  if (rule.effect === "deny") {
    const cond = rule.conditions?.recipient_domain_not_in ? ` outside ${rule.conditions.recipient_domain_not_in.join(", ")}` : "";
    return `denied${cond}`;
  }
  return rule.note ? `auto-allow (${rule.note.toLowerCase()})` : "auto-allow";
}

export function promote(agentVersionId: string, approvedBy: string): Promotion {
  const d = db();
  const diff = preflight(agentVersionId);
  if (diff.blocked) throw new Error(`Promotion blocked: ${diff.blocked}`);
  const version = d.agentVersions.find((v) => v.id === agentVersionId)!;
  const agent = d.agents.find((a) => a.id === version.agentId)!;

  // Supersede any active production deployment of this agent.
  for (const dep of d.deployments.filter(
    (x) => x.agentId === agent.id && x.environmentId === PRODUCTION_ID && x.status === "active"
  )) {
    dep.status = "superseded";
  }
  const deployment: Deployment = {
    id: id("dep"),
    agentVersionId,
    agentId: agent.id,
    environmentId: PRODUCTION_ID,
    status: "active",
    createdAt: now(),
  };
  d.deployments.push(deployment);

  const promotion: Promotion = {
    id: id("promo"),
    agentId: agent.id,
    agentVersionId,
    fromEnvironmentId: SANDBOX_ID,
    toEnvironmentId: PRODUCTION_ID,
    deploymentId: deployment.id,
    diff: { credentialChanges: diff.credentialChanges, policyDeltas: diff.policyDeltas, testStatus: diff.testStatus },
    approvedBy,
    at: now(),
  };
  d.promotions.push(promotion);
  d.auditEvents.push({
    id: id("aud"),
    ts: now(),
    actor: approvedBy,
    type: "deployment.promoted",
    message: `'${agent.name}' v${version.version} promoted Sandbox → Production (suite ${diff.testStatus.passed}/${diff.testStatus.total} green)`,
    refs: { agentId: agent.id, deploymentId: deployment.id, promotionId: promotion.id, environmentId: PRODUCTION_ID },
  });
  persist();
  return promotion;
}

/** Validate a version → environment binding (spec CUJ-2): every granted tool must exist and be permitted. */
export function validateBinding(agentVersionId: string, environmentId: string): { ok: boolean; problems: string[] } {
  const d = db();
  const version = d.agentVersions.find((v) => v.id === agentVersionId);
  if (!version) return { ok: false, problems: ["Unknown agent version"] };
  const problems: string[] = [];
  for (const tool of version.toolGrants) {
    const connector = connectorForTool(tool);
    if (!connector) {
      problems.push(`Unknown tool '${tool}'`);
      continue;
    }
    const instance = d.connectorInstances.find(
      (ci) => ci.environmentId === environmentId && ci.connectorId === connector.id
    );
    if (!instance) {
      problems.push(`'${tool}' needs the ${connector.name} connector, which is not attached to this environment`);
      continue;
    }
    if (instance.health !== "green") problems.push(`${connector.name} connector is unhealthy in this environment`);
    const { effect } = evaluatePolicy(environmentId, connector.id, tool, {});
    if (effect === "deny" && !d.policies.find((p) => p.environmentId === environmentId && p.tool === tool)?.conditions) {
      problems.push(`'${tool}' is denied outright by this environment's policy`);
    }
  }
  return { ok: problems.length === 0, problems };
}
