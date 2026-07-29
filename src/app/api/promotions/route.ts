import { NextRequest, NextResponse } from "next/server";
import { preflight, promote } from "@/server/promotion";
import { APPROVER } from "@/server/seed";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const versionId = req.nextUrl.searchParams.get("versionId");
  if (!versionId) return NextResponse.json({ error: "versionId required" }, { status: 400 });
  try {
    return NextResponse.json(preflight(versionId));
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Preflight failed" }, { status: 400 });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json();
  try {
    const promotion = promote(body.agentVersionId, body.approvedBy ?? APPROVER);
    return NextResponse.json({ promotionId: promotion.id, deploymentId: promotion.deploymentId });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Promotion failed" }, { status: 400 });
  }
}
