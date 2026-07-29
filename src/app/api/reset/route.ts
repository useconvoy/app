import { NextResponse } from "next/server";
import { resetDatabase } from "@/server/db";

export const dynamic = "force-dynamic";

// One-command reseed (spec §6.8 demo-day risk mitigation).
export async function POST() {
  resetDatabase();
  return NextResponse.json({ ok: true });
}
