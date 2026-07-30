import { NextResponse } from "next/server";
import { getAccountContext } from "@/server/control-plane/auth";
import { apiError } from "@/server/control-plane/http";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const context = await getAccountContext();
    if (!context) {
      return NextResponse.json(
        { error: "Authentication required." },
        { status: 401 },
      );
    }
    return NextResponse.json(context);
  } catch (error) {
    return apiError(error);
  }
}
