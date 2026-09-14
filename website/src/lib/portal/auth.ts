import { createHmac, randomBytes, scrypt, timingSafeEqual } from "node:crypto";

if (typeof window !== "undefined") throw new Error("Portal authentication is server-only");

export class PortalFailure extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}
export const COOKIE = "__Host-convoy_portal";
export const SESSION_SECONDS = 8 * 60 * 60;
export interface Session { nonce: string; expires: number }
function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new PortalFailure(503, "unavailable", "The portal is temporarily unavailable.");
  return value;
}
export function configuration() {
  const secret = required("PORTAL_SESSION_SECRET");
  const email = required("PORTAL_DEMO_EMAIL");
  const passwordHash = required("PORTAL_DEMO_PASSWORD_HASH");
  const origin = required("PORTAL_PUBLIC_ORIGIN");
  if (secret.length < 32 || new URL(origin).origin !== origin || !origin.startsWith("https://")) {
    throw new PortalFailure(503, "unavailable", "The portal is temporarily unavailable.");
  }
  return { secret, email, passwordHash, origin };
}
function mac(value: string) {
  const c = configuration();
  return createHmac("sha256", c.secret).update(`${c.email}\0${c.passwordHash}\0${value}`).digest();
}
function equal(a: Buffer, b: Buffer) { return a.length === b.length && timingSafeEqual(a, b); }
export function issueSession(now = Date.now()): { cookie: string; session: Session } {
  const session = { nonce: randomBytes(32).toString("hex"), expires: Math.floor(now / 1000) + SESSION_SECONDS };
  const payload = `${session.nonce}.${session.expires}`;
  return { session, cookie: `${payload}.${mac(payload).toString("base64url")}` };
}
export function readSession(request: Request, now = Date.now()): Session | null {
  const value = request.headers.get("cookie")?.split(";").map(s => s.trim()).find(s => s.startsWith(`${COOKIE}=`))?.slice(COOKIE.length + 1);
  if (!value || value.length > 180) return null;
  const match = /^([a-f0-9]{64})\.(\d{10})\.([A-Za-z0-9_-]{43})$/.exec(value);
  if (!match) return null;
  const expires = Number(match[2]);
  const seconds = Math.floor(now / 1000);
  if (expires <= seconds || expires > seconds + SESSION_SECONDS) return null;
  if (!equal(Buffer.from(mac(`${match[1]}.${match[2]}`).toString("base64url")), Buffer.from(match[3]))) return null;
  return { nonce: match[1], expires };
}
export function requireSession(request: Request) {
  const session = readSession(request);
  if (!session) throw new PortalFailure(401, "authentication_required", "Sign in to the demo portal.");
  return session;
}
export function checkOrigin(request: Request) {
  if (request.headers.get("origin") !== configuration().origin) {
    throw new PortalFailure(403, "invalid_origin", "This request could not be verified. Reload the portal and try again.");
  }
}
export function sessionCookie(value: string, maxAge = SESSION_SECONDS) {
  return `${COOKIE}=${value}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${maxAge}`;
}
let activePasswordChecks = 0;
export async function verifyLogin(email: string, password: string) {
  const c = configuration();
  const match = /^scrypt\$([a-f0-9]{32,64})\$([a-f0-9]{128})$/.exec(c.passwordHash);
  if (!match) throw new PortalFailure(503, "unavailable", "The portal is temporarily unavailable.");
  if (activePasswordChecks >= 2) throw new PortalFailure(429, "rate_limited", "Sign-in is busy. Please wait a moment and try again.");
  activePasswordChecks++;
  let key: Buffer;
  try {
    key = await new Promise<Buffer>((resolve, reject) => scrypt(password, Buffer.from(match[1], "hex"), 64, { N: 16384, r: 8, p: 1 }, (err, derived) => err ? reject(err) : resolve(derived)));
  } finally { activePasswordChecks--; }
  const passwordValid = equal(key, Buffer.from(match[2], "hex"));
  const emailValid = equal(createHmac("sha256", c.secret).update(email.toLowerCase().trim()).digest(), createHmac("sha256", c.secret).update(c.email.toLowerCase().trim()).digest());
  return passwordValid && emailValid;
}
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export function upstreamRequestId(session: Session, clientId: string) {
  if (!UUID.test(clientId)) throw new PortalFailure(422, "invalid_request", "The request identifier is invalid.");
  const bytes = mac(`chat\0${session.nonce}\0${clientId.toLowerCase()}`).subarray(0, 16);
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
