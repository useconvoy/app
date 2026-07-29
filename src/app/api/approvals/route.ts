import { NextResponse } from "next/server";
import { db } from "@/server/db";

export const dynamic = "force-dynamic";

export async function GET() {
  const d = db();
  const items = d.approvals
    .slice()
    .sort((a, b) => b.requestedAt.localeCompare(a.requestedAt))
    .map((a) => ({
      ...a,
      agent: d.agents.find((x) => x.id === a.agentId),
      environment: d.environments.find((x) => x.id === a.environmentId),
      run: (() => {
        const r = d.runs.find((x) => x.id === a.runId);
        return r ? { id: r.id, triggerType: r.triggerType, state: r.state } : undefined;
      })(),
    }));
  return NextResponse.json({ approvals: items });
}
