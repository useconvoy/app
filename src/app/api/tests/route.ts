import { NextRequest, NextResponse } from "next/server";
import { runSuite, latestSuiteStatus } from "@/server/testing/harness";

export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const body = await req.json();
  try {
    const results = await runSuite(body.agentVersionId);
    return NextResponse.json({
      results,
      passed: results.filter((r) => r.status === "pass").length,
      total: results.length,
    });
  } catch (err) {
    return NextResponse.json({ error: err instanceof Error ? err.message : "Suite failed" }, { status: 400 });
  }
}

export async function GET(req: NextRequest) {
  const versionId = req.nextUrl.searchParams.get("versionId");
  if (!versionId) return NextResponse.json({ error: "versionId required" }, { status: 400 });
  return NextResponse.json(latestSuiteStatus(versionId));
}
