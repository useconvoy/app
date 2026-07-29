import { NextRequest, NextResponse } from "next/server";
import { db, persist } from "@/server/db";
import { emit } from "@/server/events";
import { now } from "@/server/ids";
import { startRun, advanceRun } from "@/server/runtime/loop";

export const dynamic = "force-dynamic";

// Simulated HubSpot deal-stage webhook (spec P0: trigger a run via CRM
// webhook). The demo CRM's "Mark Closed-Won" button posts here.
export async function POST(req: NextRequest) {
  const body = await req.json();
  const d = db();
  const deal = d.crmDeals.find((x) => x.id === body.dealId);
  if (!deal) return NextResponse.json({ error: "deal not found" }, { status: 404 });
  const toStage = String(body.toStage ?? "closedwon");
  deal.stage = toStage;
  deal.updatedAt = now();
  persist();
  emit({ type: "system_updated", payload: { system: "crm", environmentId: deal.environmentId } });

  // Fan out to every active deployment in this environment whose trigger matches.
  const started: string[] = [];
  const skipped: string[] = [];
  for (const dep of d.deployments.filter((x) => x.environmentId === deal.environmentId && x.status === "active")) {
    const version = d.agentVersions.find((v) => v.id === dep.agentVersionId)!;
    if (version.trigger.type !== "crm_webhook") continue;
    if (version.trigger.config.event && version.trigger.config.event !== "deal.stage_changed") continue;
    if (version.trigger.config.to_stage && version.trigger.config.to_stage !== toStage) continue;
    const agent = d.agents.find((a) => a.id === dep.agentId)!;
    if (agent.paused) {
      skipped.push(`${agent.name} (paused)`);
      continue;
    }
    const run = startRun({
      deploymentId: dep.id,
      triggerType: "webhook",
      triggerPayload: { dealId: deal.id, event: "deal.stage_changed", to_stage: toStage },
    });
    void advanceRun(run.id);
    started.push(run.id);
  }
  return NextResponse.json({ ok: true, runIds: started, skipped });
}
