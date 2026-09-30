import { proxyPlatform } from "@/lib/platform/proxy";
import { limit, PortalFailure } from "@/lib/portal/auth";
import { handled, json } from "@/lib/portal/http";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Legacy health/session reader. Login and logout only use platform auth. */
export async function GET(request: Request) {
  return handled(async () => {
    limit("session-read-global", 300, 60);
    const response = await proxyPlatform(request, ["auth", "me"]);
    if (response.status === 401) return json({ authenticated: false });
    if (!response.ok) throw new PortalFailure(503, "unavailable", "The workspace connection is temporarily unavailable.");
    const result = json({ authenticated: true });
    for (const cookie of response.headers.getSetCookie()) result.headers.append("Set-Cookie", cookie);
    return result;
  });
}
