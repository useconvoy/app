/** Entry to hosted sign-in: redirects to the WorkOS authorization URL. */
import { NextResponse, type NextRequest } from "next/server";

import { authorizationUrl, workosEnabled } from "@/lib/auth/workos";

function publicOrigin(request: NextRequest): string {
  const configured = process.env.APP_URL?.trim();
  if (configured) return new URL(configured).origin;

  // Next.js sees the container's localhost URL behind Caddy. Caddy replaces
  // these forwarding headers with the public request values, so they are the
  // right fallback for an existing deployment that predates APP_URL.
  const forwardedHost = request.headers.get("x-forwarded-host")?.split(",", 1)[0]?.trim();
  const forwardedProto = request.headers.get("x-forwarded-proto")?.split(",", 1)[0]?.trim();
  if (forwardedHost && (forwardedProto === "http" || forwardedProto === "https")) {
    try {
      return new URL(`${forwardedProto}://${forwardedHost}`).origin;
    } catch {
      // Ignore malformed proxy metadata and fall back to Next.js's origin.
    }
  }

  return request.nextUrl.origin;
}

export function GET(request: NextRequest): NextResponse {
  const origin = publicOrigin(request);
  if (!workosEnabled()) {
    return NextResponse.redirect(new URL("/sign-in", origin));
  }
  const redirectUri = new URL("/api/auth/callback", origin).toString();
  return NextResponse.redirect(authorizationUrl(redirectUri));
}
