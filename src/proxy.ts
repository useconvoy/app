import { NextRequest, NextResponse } from "next/server";

// Optional shared-credential gate for hosted deployments. Set
// CONVOY_BASIC_AUTH="user:password" to require HTTP Basic auth on every
// route. This is a stopgap for demo hosting — real identity/RBAC (SSO,
// per-approver auth) is the M1 workstream in docs/architecture-v2.md.
// Unset the variable (local dev) and this is a no-op.

export default function proxy(req: NextRequest) {
  const expected = process.env.CONVOY_BASIC_AUTH;
  if (!expected) return NextResponse.next();

  // Health probes must stay unauthenticated for load balancers.
  if (req.nextUrl.pathname === "/api/health") return NextResponse.next();

  const header = req.headers.get("authorization") ?? "";
  if (header.startsWith("Basic ")) {
    try {
      const decoded = atob(header.slice(6));
      if (timingSafeEqual(decoded, expected)) return NextResponse.next();
    } catch {
      // fall through to 401
    }
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Convoy Labs"' },
  });
}

function timingSafeEqual(a: string, b: string): boolean {
  const enc = new TextEncoder();
  const ab = enc.encode(a);
  const bb = enc.encode(b);
  let diff = ab.length ^ bb.length;
  const len = Math.max(ab.length, bb.length);
  for (let i = 0; i < len; i++) diff |= (ab[i % ab.length] ?? 0) ^ (bb[i % bb.length] ?? 0);
  return diff === 0;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
