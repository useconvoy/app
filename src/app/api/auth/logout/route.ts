import { NextResponse } from "next/server";
import { clearAccountSession } from "@/server/control-plane/auth";
import { apiError } from "@/server/control-plane/http";

export const dynamic = "force-dynamic";

export async function POST() {
  try {
    await clearAccountSession();
    return NextResponse.json({ ok: true });
  } catch (error) {
    return apiError(error);
  }
}
