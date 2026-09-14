import { checkOrigin, issueSession, limit, PortalFailure, readSession, sessionCookie, verifyLogin } from "@/lib/portal/auth";
import { boundedJson, handled, json } from "@/lib/portal/http";
import { record } from "@/lib/portal/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  return handled(async () => {
    limit("session-read-global", 300, 60);
    const session = readSession(request);
    return json(session ? { authenticated: true, expires_at: new Date(session.expires * 1000).toISOString() } : { authenticated: false });
  });
}
export async function POST(request: Request) {
  return handled(async () => {
    checkOrigin(request);
    limit("login-global", 20, 60);
    const body = record(await boundedJson(request, 2048));
    if (typeof body.email !== "string" || typeof body.password !== "string" || body.email.length > 254 || body.password.length > 512) {
      throw new PortalFailure(400, "invalid_request", "Enter your demo email and password.");
    }
    limit(`login-email:${body.email.toLowerCase().trim()}`, 6, 60);
    if (!(await verifyLogin(body.email, body.password))) throw new PortalFailure(401, "invalid_credentials", "The email or password is incorrect.");
    const { cookie, session } = issueSession();
    return json({ authenticated: true, expires_at: new Date(session.expires * 1000).toISOString() }, 200, { "Set-Cookie": sessionCookie(cookie) });
  });
}
export async function DELETE(request: Request) {
  return handled(async () => {
    checkOrigin(request);
    return json({ authenticated: false }, 200, { "Set-Cookie": sessionCookie("", 0) });
  });
}
