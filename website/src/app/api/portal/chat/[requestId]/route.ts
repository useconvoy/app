import { limit, requireSession, upstreamRequestId } from "@/lib/portal/auth";
import { handled, json } from "@/lib/portal/http";
import { curateChat } from "@/lib/portal/server";
import { assertDevice, upstream, upstreamConfig } from "@/lib/portal/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET(request: Request, context: { params: Promise<{ requestId: string }> }) {
  return handled(async () => {
    const session = requireSession(request);
    limit("chat-read-global", 1200, 60);
    limit(`chat-read:${session.nonce}`, 180, 60);
    const { requestId } = await context.params;
    const upstreamId = upstreamRequestId(session, requestId);
    const { deviceId } = upstreamConfig();
    const [device, result] = await Promise.all([
      upstream(`/api/v1/devices/${deviceId}`),
      upstream(`/api/v1/devices/${deviceId}/chat/${upstreamId}`),
    ]);
    assertDevice(device, deviceId);
    return json(curateChat(result, requestId, upstreamId, deviceId));
  });
}
