import { createHash, createHmac } from "node:crypto";
import { consoleOrigin, ownCookie } from "../platform/proxy";

if (typeof window !== "undefined") throw new Error("Device authentication is server-only");

export class PortalFailure extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export interface Session { nonce: string; cookie: string }

/** Parse only. The control plane validates expiry/revocation and role on every call. */
export function requireSession(request: Request): Session {
  const cookie = ownCookie(request);
  if (!cookie) throw new PortalFailure(401, "authentication_required", "Sign in to your Convoy workspace.");
  return { cookie, nonce: createHash("sha256").update(cookie).digest("hex") };
}
export function checkOrigin(request: Request) {
  if (request.headers.get("origin") !== consoleOrigin(request) || request.headers.get("x-convoy-client") !== "web") {
    throw new PortalFailure(403, "invalid_origin", "This request could not be verified. Reload the workspace and try again.");
  }
}
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function upstreamRequestId(session: Session, clientId: string) {
  if (!UUID.test(clientId)) throw new PortalFailure(422, "invalid_request", "The request identifier is invalid.");
  // The opaque, high-entropy session credential keys the namespace. No shared
  // operator token or independent portal signing secret is needed.
  const bytes = createHmac("sha256", session.cookie).update(`chat\0${clientId.toLowerCase()}`).digest().subarray(0, 16);
  bytes[6] = (bytes[6] & 15) | 0x40;
  bytes[8] = (bytes[8] & 63) | 0x80;
  const h = bytes.toString("hex");
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}

// Bounded per-process limiter. The global bucket also bounds traffic when caller IPs change.
const rates = new Map<string, { count: number; until: number }>();
export function limit(key: string, count: number, seconds: number, now = Date.now()) {
  for (const [k, entry] of rates) if (entry.until <= now) rates.delete(k);
  let entry = rates.get(key);
  if (!entry) {
    if (rates.size >= 4096) throw new PortalFailure(429, "rate_limited", "The portal is busy. Please wait a minute and try again.");
    entry = { count: 0, until: now + seconds * 1000 }; rates.set(key, entry);
  }
  if (++entry.count > count) throw new PortalFailure(429, "rate_limited", "Too many requests. Please wait a minute and try again.");
}
