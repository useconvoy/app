/**
 * WorkOS AuthKit callback: exchange the code, sync identity, mint the
 * session, and route by org count (one -> the portal, several -> the
 * switcher, none -> onboarding). Failures land back on sign-in with a
 * generic error; codes and tokens are never logged.
 *
 * Every redirect here is built from the public origin rather than
 * request.url. Behind Caddy the latter is the container's own address, so
 * these would hand the browser https://localhost:3000/app at the one moment
 * the sign-in has otherwise just succeeded.
 */
import { NextResponse, type NextRequest } from "next/server";

import { publicOrigin } from "@/lib/auth/public-origin";
import { completeSignIn } from "@/lib/auth/sign-in";
import { exchangeCode, workosEnabled } from "@/lib/auth/workos";

export async function GET(request: NextRequest): Promise<NextResponse> {
  const origin = publicOrigin(request);
  const code = request.nextUrl.searchParams.get("code");
  if (!workosEnabled() || !code) {
    return NextResponse.redirect(new URL("/sign-in?error=auth", origin));
  }
  try {
    const identity = await exchangeCode(code);
    const destination = await completeSignIn(identity);
    return NextResponse.redirect(new URL(destination, origin));
  } catch {
    return NextResponse.redirect(new URL("/sign-in?error=auth", origin));
  }
}
