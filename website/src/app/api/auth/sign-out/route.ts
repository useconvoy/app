/** Sign out: clear the session cookie and return to sign-in. */
import { NextResponse, type NextRequest } from "next/server";

import { publicOrigin } from "@/lib/auth/public-origin";
import { destroySession } from "@/lib/auth/session";

async function signOut(request: NextRequest): Promise<NextResponse> {
  await destroySession();
  // 303 turns the sign-out form's POST into a GET of the sign-in page;
  // the default 307 would replay the POST against a page route.
  return NextResponse.redirect(new URL("/sign-in", publicOrigin(request)), 303);
}

export const GET = signOut;
export const POST = signOut;
