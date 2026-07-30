import { NextRequest, NextResponse } from "next/server";

export default async function proxy(req: NextRequest) {
  if (isPublicPath(req.nextUrl.pathname)) return NextResponse.next();

  if (process.env.CONVOY_ACCOUNTS_ENABLED === "1") {
    const secret =
      process.env.CONVOY_SESSION_SECRET ?? process.env.CONVOY_BASIC_AUTH;
    const session = req.cookies.get("convoy_session")?.value;
    if (secret && session && (await hasValidSessionSignature(session, secret))) {
      return NextResponse.next();
    }
    if (req.nextUrl.pathname.startsWith("/api/")) {
      return NextResponse.json(
        { error: "Authentication required." },
        { status: 401 },
      );
    }
    const login = new URL("/login", req.url);
    login.searchParams.set(
      "next",
      `${req.nextUrl.pathname}${req.nextUrl.search}`,
    );
    return NextResponse.redirect(login);
  }

  // Optional shared credential remains available for the seeded demo when
  // account authentication is disabled.
  const expected = process.env.CONVOY_BASIC_AUTH;
  if (!expected) return NextResponse.next();

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

function isPublicPath(pathname: string): boolean {
  return (
    [
      "/",
      "/start",
      "/login",
      "/register",
      "/robots.txt",
      "/sitemap.xml",
      "/icon",
      "/og.png",
      "/api/health",
      "/api/auth/login",
      "/api/auth/register",
    ].includes(pathname) ||
    pathname.startsWith("/_next/")
  );
}

async function hasValidSessionSignature(
  value: string,
  secret: string,
): Promise<boolean> {
  const separator = value.lastIndexOf(".");
  if (separator < 1) return false;
  const token = value.slice(0, separator);
  const supplied = value.slice(separator + 1);
  const encoder = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    encoder.encode(token),
  );
  const expected = base64Url(new Uint8Array(signature));
  return timingSafeEqual(supplied, expected);
}

function base64Url(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary)
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
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
