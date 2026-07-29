import { NextRequest, NextResponse } from "next/server";
import { db } from "@/server/db";
import { startRun, advanceRun } from "@/server/runtime/loop";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = await req.json();
  const d = db();
  let deploymentId: string | undefined = body.deploymentId;
  if (!deploymentId && body.agentId && body.environmentId) {
    deploymentId = d.deployments.find(
      (x) => x.agentId === body.agentId && x.environmentId === body.environmentId && x.status === "active"
    )?.id;
  }
  if (!deploymentId) return NextResponse.json({ error: "No active deployment found" }, { status: 400 });
  try {
    const run = startRun({
      deploymentId,
      triggerType: body.triggerType ?? "manual",
      triggerPayload: body.payload ?? {},
    });
    void advanceRun(run.id);
    return NextResponse.json({ runId: run.id });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Failed to start run" }, { status: 400 });
  }
}
