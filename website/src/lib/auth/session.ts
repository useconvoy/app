/**
 * Session handling. Identity comes from sign-in (WorkOS AuthKit, or the
 * explicit local dev provider); the active organization is a server-side
 * session fact. Sessions are signed, httpOnly cookies; nothing in them is
 * trusted from the client beyond the signature.
 */
import { SignJWT, jwtVerify } from "jose";
import { cookies } from "next/headers";

const COOKIE_NAME = "convoy_session";
const SESSION_TTL_SECONDS = 60 * 60 * 24 * 7;

export interface Session {
  userId: string;
  email: string;
  name: string;
  workosUserId?: string;
  /** Active organization; absent until the user picks or creates one. */
  orgId?: string;
}

function secret(): Uint8Array {
  const value = process.env.SESSION_SECRET;
  if (!value || value.length < 32) {
    throw new Error("SESSION_SECRET must be set to a value of at least 32 characters");
  }
  return new TextEncoder().encode(value);
}

export async function createSession(session: Session): Promise<void> {
  const token = await new SignJWT({ ...session })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt()
    .setExpirationTime(`${SESSION_TTL_SECONDS}s`)
    .sign(secret());
  const store = await cookies();
  store.set(COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: "lax",
    secure: process.env.NODE_ENV === "production",
    maxAge: SESSION_TTL_SECONDS,
    path: "/",
  });
}

export async function getSession(): Promise<Session | null> {
  const store = await cookies();
  const token = store.get(COOKIE_NAME)?.value;
  if (!token) return null;
  try {
    const { payload } = await jwtVerify(token, secret());
    const { userId, email, name, workosUserId, orgId } = payload as Record<string, unknown>;
    if (typeof userId !== "string" || typeof email !== "string" || typeof name !== "string") {
      return null;
    }
    return {
      userId,
      email,
      name,
      workosUserId: typeof workosUserId === "string" ? workosUserId : undefined,
      orgId: typeof orgId === "string" ? orgId : undefined,
    };
  } catch {
    return null;
  }
}

/** Session or a thrown redirect-worthy error; route handlers map it to 401. */
export async function requireSession(): Promise<Session> {
  const session = await getSession();
  if (!session) throw new UnauthenticatedError();
  return session;
}

/** Session with an active org; the portal cannot operate without one. */
export async function requireOrgSession(): Promise<Session & { orgId: string }> {
  const session = await requireSession();
  if (!session.orgId) throw new NoActiveOrgError();
  return session as Session & { orgId: string };
}

export async function setActiveOrg(orgId: string): Promise<void> {
  const session = await requireSession();
  await createSession({ ...session, orgId });
}

export async function destroySession(): Promise<void> {
  const store = await cookies();
  store.delete(COOKIE_NAME);
}

export class UnauthenticatedError extends Error {
  constructor() {
    super("not signed in");
  }
}

export class NoActiveOrgError extends Error {
  constructor() {
    super("no active organization");
  }
}
