import { PortalFailure } from "./auth";

if (typeof window !== "undefined") throw new Error("Portal upstream access is server-only");
export const record = (value: unknown): Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
export const list = (value: unknown): unknown[] => Array.isArray(value) ? value : [];
export const str = (value: unknown): string | null => typeof value === "string" ? value : null;
export const num = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
export function upstreamConfig() {
  const base = process.env.CONVOY_UPSTREAM_URL;
  const token = process.env.CONVOY_UPSTREAM_TOKEN;
  const deviceId = process.env.CONVOY_DEVICE_ID;
  if (!base || !token?.startsWith("cva_") || !deviceId || !/^dev_[a-z0-9]+$/.test(deviceId)) {
    throw new PortalFailure(503, "unavailable", "The device connection is unavailable.");
  }
  const url = new URL(base);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash || url.pathname !== "/") {
    throw new PortalFailure(503, "unavailable", "The device connection is unavailable.");
  }
  return { origin: url.origin, token, deviceId };
}
// Keep credentials confined to this exact route set, even if a caller passes an unexpected path.
export function allowedPath(path: string, method: string, deviceId: string): boolean {
  const d = `/api/v1/devices/${deviceId}`;
  if (method === "POST") return path === `${d}/chat`;
  if (method !== "GET") return false;
  return path === d || path === "/api/v1/chat/devices" || path === "/api/v1/settings"
    || path === `${d}/telemetry?limit=120` || path === `${d}/spans?limit=100`
    || /^\/api\/v1\/usage\?from=\d{4}-\d{2}-\d{2}&to=\d{4}-\d{2}-\d{2}$/.test(path)
    || /^\/api\/v1\/releases\/[a-zA-Z0-9_-]{1,64}$/.test(path)
    || new RegExp(`^${d}/chat/[a-f0-9-]{36}$`).test(path);
}
export async function upstream(path: string, method = "GET", body?: unknown): Promise<unknown> {
  const c = upstreamConfig();
  if (!allowedPath(path, method, c.deviceId)) throw new PortalFailure(400, "invalid_request", "This action is unavailable.");
  try {
    const response = await fetch(`${c.origin}${path}`, {
      method, cache: "no-store", redirect: "error", signal: AbortSignal.timeout(8000),
      headers: { Authorization: `Bearer ${c.token}`, Accept: "application/json", ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    if (!response.ok) {
      await response.body?.cancel();
      const errors: Record<number, [string, string]> = {
        404: ["not_found", "This request is not available. The device may not have received it yet."],
        409: ["device_busy", "The device or active release changed, or another request is running. Refresh before sending again."],
        413: ["request_too_large", "The conversation is too large."],
        422: ["invalid_request", "The request could not be accepted. Check the message and output limit."],
        429: ["rate_limited", "The device is busy. Wait before sending another request."],
      };
      const safe = errors[response.status];
      throw new PortalFailure(safe ? response.status : 503, safe?.[0] ?? "upstream_unavailable", safe?.[1] ?? "The device connection is temporarily unavailable.");
    }
    const reader = response.body?.getReader();
    if (!reader) throw new Error("missing body");
    const chunks: Uint8Array[] = []; let size = 0;
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 2 * 1024 * 1024) { await reader.cancel(); throw new Error("response too large"); }
        chunks.push(value);
      }
    } finally { reader.releaseLock(); }
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch (error) {
    if (error instanceof PortalFailure) throw error;
    throw new PortalFailure(503, "upstream_unavailable", "The device connection is temporarily unavailable. Check the same request before sending another message.");
  }
}
export function assertDevice(value: unknown, deviceId: string) {
  const d = record(value);
  if (d.id !== deviceId || d.simulated !== false) throw new PortalFailure(503, "device_unavailable", "The configured device is unavailable.");
  return d;
}
