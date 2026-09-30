import { limit, requireSession } from "@/lib/portal/auth";
import { handled, json } from "@/lib/portal/http";
import { snapshot } from "@/lib/portal/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(request: Request) {
  return handled(async () => {
    const session = requireSession(request);
    limit("snapshot-global", 120, 60);
    limit(`snapshot:${session.nonce}`, 30, 60);
    return json(await snapshot(session));
  });
}
