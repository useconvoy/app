import { NextRequest } from "next/server";
import { sseResponse } from "@/server/events";
import { db } from "@/server/db";

export const dynamic = "force-dynamic";

export function GET(req: NextRequest): Response {
  db(); // ensure seeded
  const runId = req.nextUrl.searchParams.get("runId");
  return sseResponse(runId ? (e) => e.runId === runId || e.type === "approval_decided" : undefined);
}
