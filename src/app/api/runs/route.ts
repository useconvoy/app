import { NextRequest, NextResponse } from "next/server";
import { db, persist } from "@/server/db";
import {
  isCloudRuntimeEnabled,
  startCloudMission,
} from "@/server/cloud-runtime";
import { startRun, advanceRun, finishRun } from "@/server/runtime/loop";

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
    if (isCloudRuntimeEnabled() && run.triggerType !== "test") {
      run.provider = "temporal-fargate";
      run.state = "running";
      persist();
      try {
        const agent = d.agents.find((item) => item.id === run.agentId);
        const requestedObjective =
          typeof run.triggerPayload.objective === "string"
            ? run.triggerPayload.objective
            : undefined;
        await startCloudMission(
          run.id,
          requestedObjective ??
            `Run ${agent?.name ?? "the deployed enterprise agent"} and synthesize an evidence-backed result.`,
        );
      } catch (error) {
        finishRun(
          run,
          "failed",
          error instanceof Error
            ? `Cloud mission failed to start: ${error.message}`
            : "Cloud mission failed to start.",
        );
        throw error;
      }
    } else {
      run.provider = "local";
      persist();
      void advanceRun(run.id);
    }
    return NextResponse.json({ runId: run.id });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Failed to start run" }, { status: 400 });
  }
}
