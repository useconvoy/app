import { NextResponse } from "next/server";
import { db } from "@/server/db";

export const dynamic = "force-dynamic";

export async function GET(_req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const d = db();
  const run = d.runs.find((r) => r.id === id);
  if (!run) return NextResponse.json({ error: "not found" }, { status: 404 });
  const { modelMessages: _mm, ...runView } = run;
  return NextResponse.json({
    run: runView,
    agent: d.agents.find((a) => a.id === run.agentId),
    version: d.agentVersions.find((v) => v.id === run.agentVersionId),
    environment: d.environments.find((e) => e.id === run.environmentId),
    toolCalls: d.toolCalls.filter((t) => t.runId === id).sort((a, b) => a.seq - b.seq),
    approvals: d.approvals.filter((a) => a.runId === id),
  });
}
