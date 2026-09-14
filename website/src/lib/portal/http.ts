import { PortalFailure } from "./auth";

export function json(value: unknown, status = 200, headers: Record<string, string> = {}) {
  return Response.json(value, { status, headers: { "Cache-Control": "private, no-store, max-age=0", "Pragma": "no-cache", "X-Content-Type-Options": "nosniff", ...headers } });
}
export async function handled(fn: () => Promise<Response>): Promise<Response> {
  try { return await fn(); }
  catch (error) {
    const e = error instanceof PortalFailure ? error : new PortalFailure(503, "unavailable", "The portal is temporarily unavailable.");
    return json({ error: { code: e.code, message: e.message } }, e.status, e.status === 429 ? { "Retry-After": "60" } : {});
  }
}
export async function boundedJson(request: Request, maxBytes: number): Promise<unknown> {
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    throw new PortalFailure(415, "invalid_content_type", "Send a JSON request.");
  }
  if (Number(request.headers.get("content-length")) > maxBytes) throw new PortalFailure(413, "request_too_large", "The request is too large.");
  const reader = request.body?.getReader();
  if (!reader) throw new PortalFailure(400, "invalid_request", "The request body is missing.");
  const chunks: Uint8Array[] = []; let size = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      reject(new PortalFailure(408, "request_timeout", "The request took too long. Please try again."));
      void reader.cancel().catch(() => {});
    }, 8000);
  });
  try {
    return await Promise.race([deadline, (async () => {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > maxBytes) { await reader.cancel(); throw new PortalFailure(413, "request_too_large", "The request is too large."); }
        chunks.push(value);
      }
      return JSON.parse(Buffer.concat(chunks).toString("utf8"));
    })()]);
  } catch (error) {
    if (error instanceof PortalFailure) throw error;
    throw new PortalFailure(400, "invalid_request", "The request must contain valid JSON.");
  } finally { clearTimeout(timer); reader.releaseLock(); }
}
