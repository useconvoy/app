import { NextRequest, NextResponse } from "next/server";
import { decideApproval } from "@/server/runtime/loop";
import { APPROVER } from "@/server/seed";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json();
  const decision = body.decision === "reject" ? "reject" : "approve";
  // Fire and return quickly — the resumed run streams over SSE.
  void decideApproval(id, decision, body.approver ?? APPROVER);
  return NextResponse.json({ ok: true });
}
