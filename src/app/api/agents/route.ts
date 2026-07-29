import { NextRequest, NextResponse } from "next/server";
import { db, persist } from "@/server/db";
import { id, now } from "@/server/ids";
import { validateBinding } from "@/server/promotion";
import { SANDBOX_ID } from "@/server/seed";
import type { Agent, AgentVersion, Deployment } from "@/server/types";

export const dynamic = "force-dynamic";

// CUJ-2: the lightweight builder — create an agent (from scratch or template)
// as a versioned workspace asset and bind v1 to Sandbox.
export async function POST(req: NextRequest) {
  const body = await req.json();
  const d = db();
  if (!body.name || !body.instructions || !Array.isArray(body.toolGrants) || body.toolGrants.length === 0) {
    return NextResponse.json({ error: "name, instructions, and at least one tool grant are required" }, { status: 400 });
  }

  const agent: Agent = {
    id: id("agent"),
    name: String(body.name),
    emoji: String(body.emoji ?? "🤖"),
    description: String(body.description ?? ""),
    templateId: body.templateId || undefined,
    paused: false,
    createdAt: now(),
  };
  const version: AgentVersion = {
    id: id("ver"),
    agentId: agent.id,
    version: 1,
    instructions: String(body.instructions),
    toolGrants: body.toolGrants.map(String),
    params: body.params ?? {},
    trigger: body.trigger ?? { type: "manual", config: {} },
    createdAt: now(),
  };

  // Binding validates every granted tool is available and permitted in Sandbox.
  const validation = validateBinding0(version, SANDBOX_ID);
  if (!validation.ok) {
    return NextResponse.json({ error: "Binding validation failed", problems: validation.problems }, { status: 400 });
  }

  d.agents.push(agent);
  d.agentVersions.push(version);
  const deployment: Deployment = {
    id: id("dep"),
    agentVersionId: version.id,
    agentId: agent.id,
    environmentId: SANDBOX_ID,
    status: "active",
    createdAt: now(),
  };
  d.deployments.push(deployment);
  d.auditEvents.push({
    id: id("aud"),
    ts: now(),
    actor: body.actor ?? "Operator",
    type: "agent.created",
    message: `Agent '${agent.name}' created (v1) and bound to Sandbox`,
    refs: { agentId: agent.id, deploymentId: deployment.id, environmentId: SANDBOX_ID },
  });
  persist();
  return NextResponse.json({ agentId: agent.id, versionId: version.id, deploymentId: deployment.id });
}

// validateBinding works off a persisted version; wrap for a not-yet-saved one.
function validateBinding0(version: AgentVersion, environmentId: string) {
  const d = db();
  d.agentVersions.push(version);
  try {
    return validateBinding(version.id, environmentId);
  } finally {
    d.agentVersions.pop();
  }
}
