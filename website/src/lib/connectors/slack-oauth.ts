/**
 * The Slack OAuth install, org-level by construction: an admin clicks Add
 * to Slack, Slack's own consent screen asks which workspace to install
 * the app into, and the exchange hands back a workspace bot token that
 * lands on the organization's one Slack connection, exactly where a
 * pasted token goes. Nothing here is per-user: the token belongs to the
 * installed app in the workspace, and every Agent in the org runs
 * through it.
 *
 * The state parameter is HMAC-signed over the initiating org and admin
 * plus a nonce that also rides a browser cookie, so a callback can only
 * complete the install the same admin started from the same browser.
 */
import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";

/** The scopes backing the connector's declared tools, nothing more. */
export const SLACK_SCOPES = ["channels:read", "channels:history", "chat:write"] as const;

const STATE_TTL_MS = 10 * 60 * 1000;

export interface SlackOAuthState {
  orgId: string;
  userId: string;
  nonce: string;
  exp: number;
}

export function slackOAuthConfigured(): boolean {
  return Boolean(process.env.SLACK_CLIENT_ID && process.env.SLACK_CLIENT_SECRET);
}

export function newNonce(): string {
  return randomBytes(16).toString("base64url");
}

function hmac(payload: string, secret: string): string {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}

/** Pack and sign the state for the round trip through Slack. */
export function signState(
  input: { orgId: string; userId: string; nonce: string },
  secret: string,
  now = Date.now(),
): string {
  const payload = Buffer.from(
    JSON.stringify({ ...input, exp: now + STATE_TTL_MS } satisfies SlackOAuthState),
  ).toString("base64url");
  return `${payload}.${hmac(payload, secret)}`;
}

/** Verify signature and expiry; null means the state cannot be trusted. */
export function verifyState(
  state: string,
  secret: string,
  now = Date.now(),
): SlackOAuthState | null {
  const dot = state.lastIndexOf(".");
  if (dot <= 0) return null;
  const payload = state.slice(0, dot);
  const signature = state.slice(dot + 1);
  const expected = hmac(payload, secret);
  const given = Buffer.from(signature);
  const wanted = Buffer.from(expected);
  if (given.length !== wanted.length || !timingSafeEqual(given, wanted)) return null;
  try {
    const parsed = JSON.parse(Buffer.from(payload, "base64url").toString()) as SlackOAuthState;
    if (typeof parsed.orgId !== "string" || typeof parsed.userId !== "string") return null;
    if (typeof parsed.nonce !== "string" || typeof parsed.exp !== "number") return null;
    if (parsed.exp < now) return null;
    return parsed;
  } catch {
    return null;
  }
}

export function slackAuthorizeUrl(input: {
  clientId: string;
  redirectUri: string;
  state: string;
}): string {
  const url = new URL("https://slack.com/oauth/v2/authorize");
  url.searchParams.set("client_id", input.clientId);
  url.searchParams.set("scope", SLACK_SCOPES.join(","));
  url.searchParams.set("redirect_uri", input.redirectUri);
  url.searchParams.set("state", input.state);
  return url.toString();
}

export interface SlackTokenExchange {
  botToken: string;
  teamName: string;
}

/** Exchange the callback code for the workspace bot token. */
export async function exchangeSlackCode(input: {
  code: string;
  redirectUri: string;
}): Promise<SlackTokenExchange> {
  const response = await fetch("https://slack.com/api/oauth.v2.access", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code: input.code,
      client_id: process.env.SLACK_CLIENT_ID ?? "",
      client_secret: process.env.SLACK_CLIENT_SECRET ?? "",
      redirect_uri: input.redirectUri,
    }),
  });
  const body = (await response.json()) as {
    ok: boolean;
    error?: string;
    access_token?: string;
    team?: { name?: string };
  };
  if (!body.ok || !body.access_token) {
    throw new Error(`slack oauth exchange failed: ${body.error ?? response.status}`);
  }
  return { botToken: body.access_token, teamName: body.team?.name ?? "" };
}

/** The public callback address; APP_URL is the deployment's origin. */
export function slackRedirectUri(): string {
  const base = (process.env.APP_URL ?? "").trim().replace(/\/+$/, "");
  if (!base) throw new Error("APP_URL must be set for the Slack install");
  return `${base}/api/connectors/slack/callback`;
}
