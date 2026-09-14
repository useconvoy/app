import { checkOrigin, limit, requireSession, upstreamRequestId } from "@/lib/portal/auth";
import { boundedJson, handled, json } from "@/lib/portal/http";
import { chatInput, curateChat } from "@/lib/portal/server";
import { assertDevice, upstream, upstreamConfig } from "@/lib/portal/upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(request: Request) {
  return handled(async () => {
    checkOrigin(request);
    const session = requireSession(request);
    limit("chat-global", 20, 60);
    limit(`chat:${session.nonce}`, 6, 60);
    const input = chatInput(await boundedJson(request, 65536));
    const { deviceId } = upstreamConfig();
    assertDevice(await upstream(`/api/v1/devices/${deviceId}`), deviceId);
    const upstreamId = upstreamRequestId(session, input.request_id);
    const result = await upstream(`/api/v1/devices/${deviceId}/chat`, "POST", { ...input, request_id: upstreamId });
    return json(curateChat(result, input.request_id, upstreamId, deviceId), 202);
  });
}
