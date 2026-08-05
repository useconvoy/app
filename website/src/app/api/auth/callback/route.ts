/**
 * WorkOS AuthKit callback: exchange the code, sync identity, mint the
 * session, and route by org count (one -> the portal, several -> the
 * switcher, none -> onboarding). Failures land back on sign-in with a
 * generic error; codes and tokens are never logged.
 */
import { NextResponse, type NextRequest } from "next/server";

import { completeSignIn } from "@/lib/auth/sign-in";
import { exchangeCode, workosEnabled } from "@/lib/auth/workos";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const code = request.nextUrl.searchParams.get("code");
  if (!workosEnabled() || !code) {
    return NextResponse.redirect(new URL("/sign-in?error=auth", request.url));
  }
  try {
    const identity = await exchangeCode(code);
    const destination = await completeSignIn(identity);
    return NextResponse.redirect(new URL(destination, request.url));
  } catch {
    return NextResponse.redirect(new URL("/sign-in?error=auth", request.url));
  }
}
