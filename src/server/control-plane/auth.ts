import {
  createHash,
  createHmac,
  randomBytes,
  timingSafeEqual,
} from "node:crypto";
import { cookies } from "next/headers";
import {
  createSession,
  deleteSession,
  getAccount,
  getSession,
  listWorkspacesForAccount,
} from "./store";
import { publicAccount, type Account, type AccountContext } from "./types";

export const SESSION_COOKIE = "convoy_session";
const SESSION_DAYS = 30;

export class AuthenticationError extends Error {}

function sessionSecret(): string {
  const configured =
    process.env.CONVOY_SESSION_SECRET ?? process.env.CONVOY_BASIC_AUTH;
  if (configured) return configured;
  if (process.env.NODE_ENV === "production") {
    throw new Error(
      "CONVOY_SESSION_SECRET is required when account authentication is enabled.",
    );
  }
  return "convoy-local-development-session-secret";
}

function tokenHash(token: string): string {
  return createHash("sha256").update(token).digest("hex");
}

function tokenSignature(token: string): string {
  return createHmac("sha256", sessionSecret())
    .update(token)
    .digest("base64url");
}

function signedToken(token: string): string {
  return `${token}.${tokenSignature(token)}`;
}

function parseSignedToken(value: string | undefined): string | null {
  if (!value) return null;
  const separator = value.lastIndexOf(".");
  if (separator < 1) return null;
  const token = value.slice(0, separator);
  const supplied = Buffer.from(value.slice(separator + 1));
  const expected = Buffer.from(tokenSignature(token));
  if (
    supplied.length !== expected.length ||
    !timingSafeEqual(supplied, expected)
  ) {
    return null;
  }
  return token;
}

export async function createAccountSession(accountId: string): Promise<void> {
  const token = randomBytes(32).toString("base64url");
  const createdAt = new Date();
  const expiresAt = new Date(
    createdAt.getTime() + SESSION_DAYS * 24 * 60 * 60 * 1000,
  );
  await createSession({
    tokenHash: tokenHash(token),
    accountId,
    createdAt: createdAt.toISOString(),
    expiresAt: expiresAt.toISOString(),
  });
  const cookieStore = await cookies();
  cookieStore.set(SESSION_COOKIE, signedToken(token), {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    path: "/",
    expires: expiresAt,
  });
}

export async function clearAccountSession(): Promise<void> {
  const cookieStore = await cookies();
  const raw = parseSignedToken(cookieStore.get(SESSION_COOKIE)?.value);
  if (raw) await deleteSession(tokenHash(raw));
  cookieStore.delete(SESSION_COOKIE);
}

export async function currentAccount(): Promise<Account | null> {
  const cookieStore = await cookies();
  const raw = parseSignedToken(cookieStore.get(SESSION_COOKIE)?.value);
  if (!raw) return null;
  const session = await getSession(tokenHash(raw));
  if (!session || Date.parse(session.expiresAt) <= Date.now()) {
    if (session) await deleteSession(session.tokenHash);
    return null;
  }
  return getAccount(session.accountId);
}

export async function requireAccount(): Promise<Account> {
  const account = await currentAccount();
  if (!account) throw new AuthenticationError("Authentication required.");
  return account;
}

export async function getAccountContext(): Promise<AccountContext | null> {
  const account = await currentAccount();
  if (!account) return null;
  return {
    account: publicAccount(account),
    workspaces: await listWorkspacesForAccount(account.id),
  };
}
