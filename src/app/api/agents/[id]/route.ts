import { NextResponse } from "next/server";
import { db } from "@/server/db";
import { latestSuiteStatus } from "@/server/testing/harness";
import { SANDBOX_ID, PRODUCTION_ID, DEMO_SANDBOX_DEAL_ID } from "@/server/seed";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const d = db();
  const agent = d.agents.find((a) => a.id === id);
  if (!agent) return NextResponse.json({ error: "not found" }, { status: 404 });
  const versions = d.agentVersions.filter((v) => v.agentId === id).sort((a, b) => b.version - a.version);
  const latest = versions[0];
  const deployments = d.deployments
    .filter((x) => x.agentId === id)
    .map((x) => ({ ...x, environment: d.environments.find((e) => e.id === x.environmentId) }));
  const scenarios = d.testScenarios.filter((s) => s.agentTemplateId === agent.templateId);
  const runs = d.runs
    .filter((r) => r.agentId === id)
    .sort((a, b) => b.startedAt.localeCompare(a.startedAt))
    .slice(0, 12)
    .map(({ modelMessages: _mm, ...r }) => r);
  const sandboxDeal = d.crmDeals.find((x) => x.id === DEMO_SANDBOX_DEAL_ID);
  return NextResponse.json({
    agent,
    versions,
    latestVersion: latest,
    deployments,
    scenarios,
    suite: latest ? latestSuiteStatus(latest.id) : { total: 0, passed: 0, runs: [] },
    promotions: d.promotions.filter((p) => p.agentId === id),
    runs,
    sandboxEnvId: SANDBOX_ID,
    productionEnvId: PRODUCTION_ID,
    demoSandboxDeal: sandboxDeal ? { id: sandboxDeal.id, name: sandboxDeal.name, stage: sandboxDeal.stage } : null,
  });
}
