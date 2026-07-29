import { NextRequest, NextResponse } from "next/server";
import { db, persist } from "@/server/db";
import { emit } from "@/server/events";
import { id, now } from "@/server/ids";

export const dynamic = "force-dynamic";

// Fleet-wide kill switch: pause/resume an agent across every environment.
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id: agentId } = await ctx.params;
  const body = await req.json().catch(() => ({}));
  const d = db();
  const agent = d.agents.find((a) => a.id === agentId);
  if (!agent) return NextResponse.json({ error: "not found" }, { status: 404 });
  agent.paused = body.paused ?? !agent.paused;
  d.auditEvents.push({
    id: id("aud"),
    ts: now(),
    actor: body.actor ?? "Operator",
    type: agent.paused ? "agent.paused" : "agent.resumed",
    message: agent.paused
      ? `KILL SWITCH: '${agent.name}' paused fleet-wide — no new runs, in-flight runs stop at their next action`
      : `'${agent.name}' resumed fleet-wide`,
    refs: { agentId: agent.id },
  });
  persist();
  emit({ type: "run_updated", payload: { agentId: agent.id, paused: agent.paused } });
  return NextResponse.json({ paused: agent.paused });
}
