/** WorkOS AuthKit entry: send the browser to the hosted sign-in page. */
import { NextResponse, type NextRequest } from "next/server";

import { publicOrigin } from "@/lib/auth/public-origin";
import { authorizationUrl, workosEnabled } from "@/lib/auth/workos";

export function GET(request: NextRequest): NextResponse {
  const origin = publicOrigin(request);
  if (!workosEnabled()) {
    return NextResponse.redirect(new URL("/sign-in", origin));
  }
  const redirectUri = new URL("/api/auth/callback", origin).toString();
  return NextResponse.redirect(authorizationUrl(redirectUri));
}
