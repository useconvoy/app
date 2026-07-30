import { NextResponse } from "next/server";
import { db, persist } from "@/server/db";
import { readCloudMission } from "@/server/cloud-runtime";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const d = db();
  const run = d.runs.find((r) => r.id === id);
  if (!run) return NextResponse.json({ error: "not found" }, { status: 404 });
  let cloudMission;
  if (run.provider === "temporal-fargate") {
    try {
      cloudMission = await readCloudMission(run.id);
      if (cloudMission.status === "COMPLETED" && run.state !== "succeeded") {
        run.state = "succeeded";
        run.summary = `${cloudMission.mission?.totalAgents ?? cloudMission.agents.length} policy-governed agent episodes completed in isolated AWS Fargate tasks.`;
        run.endedAt = cloudMission.mission?.completedAt ?? new Date().toISOString();
        persist();
      } else if (cloudMission.agents.length > 0 && run.state === "queued") {
        run.state = "running";
        persist();
      }
    } catch (error) {
      console.error("Unable to refresh cloud mission", error);
    }
  }
  const { modelMessages: _mm, ...runView } = run;
  return NextResponse.json({
    run: runView,
    agent: d.agents.find((a) => a.id === run.agentId),
    version: d.agentVersions.find((v) => v.id === run.agentVersionId),
    environment: d.environments.find((e) => e.id === run.environmentId),
    toolCalls: d.toolCalls.filter((t) => t.runId === id).sort((a, b) => a.seq - b.seq),
    approvals: d.approvals.filter((a) => a.runId === id),
    cloudMission,
  });
}
