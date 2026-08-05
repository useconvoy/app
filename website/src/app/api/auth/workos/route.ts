/** Entry to hosted sign-in: redirects to the WorkOS authorization URL. */
import { NextResponse, type NextRequest } from "next/server";

import { authorizationUrl, workosEnabled } from "@/lib/auth/workos";

export function GET(request: NextRequest): NextResponse {
  if (!workosEnabled()) {
    return NextResponse.redirect(new URL("/sign-in", request.url));
  }
  const redirectUri = new URL("/api/auth/callback", request.url).toString();
  return NextResponse.redirect(authorizationUrl(redirectUri));
}
