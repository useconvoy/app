/**
 * The browser legs of a hosted connector install, provider-generic. The
 * website is only the public edge here: it knows which signed-in admin of
 * which org initiated (the session), signs that into the state that rides
 * through the provider's consent screen, and hands the returning code to
 * the environments registry, which holds the provider app's client
 * credentials, performs the exchange, and stores the resulting credential
 * on the organization's connection. Provider knowledge (consent URL,
 * scopes, client id) arrives from the registry's provider directory; no
 * provider specifics live website-side.
 *
 * The state is HMAC-signed over the initiating org and admin plus a nonce
 * that also rides a browser cookie, so a callback can only complete the
 * install the same admin started from the same browser.
 */
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

const STATE_TTL_MS = 10 * 60 * 1000;

export interface InstallState {
  orgId: string;
  userId: string;
  nonce: string;
  exp: number;
}

export interface DirectoryOAuth {
  authorizeUrl: string;
  clientId: string;
  scopes: string[];
}

export function newNonce(): string {
  return randomBytes(16).toString("base64url");
}

function hmac(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

/** Pack and sign the state for the round trip through the provider. */
export function signState(
  input: { orgId: string; userId: string; nonce: string },
  secret: string,
  now = Date.now(),
): string {
  const payload = Buffer.from(
    JSON.stringify({ ...input, exp: now + STATE_TTL_MS } satisfies InstallState),
  ).toString("base64url");
  return `${payload}.${hmac(payload, secret)}`;
}

/** Verify signature and expiry; null means the state cannot be trusted. */
export function verifyState(
  state: string,
  secret: string,
  now = Date.now(),
): InstallState | null {
  const dot = state.lastIndexOf(".");
  if (dot <= 0) return null;
  const payload = state.slice(0, dot);
  const signature = state.slice(dot + 1);
  const expected = hmac(payload, secret);
  const given = Buffer.from(signature);
  const wanted = Buffer.from(expected);
  if (given.length !== wanted.length || !timingSafeEqual(given, wanted)) return null;
  try {
    const parsed = JSON.parse(Buffer.from(payload, "base64url").toString()) as InstallState;
    if (typeof parsed.orgId !== "string" || typeof parsed.userId !== "string") return null;
    if (typeof parsed.nonce !== "string" || typeof parsed.exp !== "number") return null;
    if (parsed.exp < now) return null;
    return parsed;
  } catch {
    return null;
  }
}

/** The provider consent URL, from the directory's registry-served pieces. */
export function authorizeUrl(
  oauth: DirectoryOAuth,
  input: { redirectUri: string; state: string },
): string {
  const url = new URL(oauth.authorizeUrl);
  url.searchParams.set("client_id", oauth.clientId);
  url.searchParams.set("scope", oauth.scopes.join(","));
  url.searchParams.set("redirect_uri", input.redirectUri);
  url.searchParams.set("state", input.state);
  return url.toString();
}

/** The public callback address; APP_URL is the deployment's origin. */
export function installRedirectUri(provider: string): string {
  const base = (process.env.APP_URL ?? "").trim().replace(/\/+$/, "");
  if (!base) throw new Error("APP_URL must be set for hosted installs");
  return `${base}/api/connectors/${provider}/callback`;
}
