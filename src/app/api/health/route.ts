import { NextResponse } from "next/server";
import { db } from "@/server/db";

export const dynamic = "force-dynamic";

// Liveness/readiness probe for containers and load balancers.
export async function GET() {
  try {
    const d = db();
    return NextResponse.json({
      ok: true,
      agents: d.agents.length,
      environments: d.environments.length,
      liveModel: Boolean(process.env.ANTHROPIC_API_KEY) && process.env.CONVOY_LIVE_MODEL === "1",
    });
  } catch {
    return NextResponse.json({ ok: false }, { status: 503 });
  }
}
