/** Sign out: clear the session cookie and return to sign-in. */
import { NextResponse, type NextRequest } from "next/server";

import { destroySession } from "@/lib/auth/session";

async function signOut(request: NextRequest): Promise<NextResponse> {
  await destroySession();
  return NextResponse.redirect(new URL("/sign-in", request.url));
}

export const GET = signOut;
export const POST = signOut;
