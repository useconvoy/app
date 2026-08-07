// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  authorizationUrl: vi.fn((redirectUri: string) => {
    const url = new URL("https://auth.example/authorize");
    url.searchParams.set("redirect_uri", redirectUri);
    return url.toString();
  }),
  workosEnabled: vi.fn(() => true),
  exchangeCode: vi.fn(async () => ({ id: "user_1", email: "a@example.com" })),
}));

const signIn = vi.hoisted(() => ({
  completeSignIn: vi.fn(async () => "/app"),
}));

const session = vi.hoisted(() => ({
  destroySession: vi.fn(async () => undefined),
}));

vi.mock("@/lib/auth/workos", () => auth);
vi.mock("@/lib/auth/sign-in", () => signIn);
vi.mock("@/lib/auth/session", () => session);

import { NextRequest } from "next/server";

import { GET as callback } from "@/app/api/auth/callback/route";
import { GET as signOut } from "@/app/api/auth/sign-out/route";
import { GET } from "@/app/api/auth/workos/route";

/** The container's own address, which is all Next.js sees behind Caddy. */
const CONTAINER = "https://localhost:3000";
const PROXIED = {
  "x-forwarded-host": "deployconvoy.com",
  "x-forwarded-proto": "https",
};

function request(headers: Record<string, string> = {}, path = "/api/auth/workos"): NextRequest {
  return new NextRequest(`${CONTAINER}${path}`, { headers });
}

describe("WorkOS entry route", () => {
  beforeEach(() => {
    delete process.env.APP_URL;
    auth.authorizationUrl.mockClear();
    auth.workosEnabled.mockReturnValue(true);
  });

  afterEach(() => {
    delete process.env.APP_URL;
  });

  it("uses the configured public origin for the callback", () => {
    process.env.APP_URL = "https://deployconvoy.com/ignored-path";

    const response = GET(request());

    expect(response.status).toBe(307);
    expect(auth.authorizationUrl).toHaveBeenCalledWith(
      "https://deployconvoy.com/api/auth/callback",
    );
  });

  it("uses trusted proxy headers when APP_URL is absent", () => {
    const response = GET(request(PROXIED));

    expect(response.status).toBe(307);
    expect(auth.authorizationUrl).toHaveBeenCalledWith(
      "https://deployconvoy.com/api/auth/callback",
    );
  });

  it("falls back to the request origin without proxy metadata", () => {
    GET(request());

    expect(auth.authorizationUrl).toHaveBeenCalledWith(
      "https://localhost:3000/api/auth/callback",
    );
  });

  it("uses the public origin when WorkOS is disabled", () => {
    auth.workosEnabled.mockReturnValue(false);

    const response = GET(request(PROXIED));

    expect(response.headers.get("location")).toBe("https://deployconvoy.com/sign-in");
    expect(auth.authorizationUrl).not.toHaveBeenCalled();
  });

  it("keeps serving on the fallback when APP_URL is malformed", () => {
    process.env.APP_URL = "not a url";
    const warn = vi.spyOn(console, "error").mockImplementation(() => undefined);

    GET(request(PROXIED));

    // A typo in one environment variable must not lock everyone out of
    // signing in, but it must not pass silently either.
    expect(auth.authorizationUrl).toHaveBeenCalledWith(
      "https://deployconvoy.com/api/auth/callback",
    );
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

/*
 * The callback is where the original bug actually bites hardest: correcting
 * only the entry route gets WorkOS to accept the redirect URI and then hands
 * the browser a container-local address at the moment sign-in has succeeded.
 */
describe("WorkOS callback route", () => {
  beforeEach(() => {
    delete process.env.APP_URL;
    auth.workosEnabled.mockReturnValue(true);
    signIn.completeSignIn.mockClear();
    signIn.completeSignIn.mockResolvedValue("/app");
  });

  afterEach(() => {
    delete process.env.APP_URL;
  });

  it("sends the signed-in browser to the public origin", async () => {
    process.env.APP_URL = "https://deployconvoy.com";

    const response = await callback(request({}, "/api/auth/callback?code=abc"));

    expect(response.headers.get("location")).toBe("https://deployconvoy.com/app");
  });

  it("uses proxy headers for the destination when APP_URL is absent", async () => {
    const response = await callback(request(PROXIED, "/api/auth/callback?code=abc"));

    expect(response.headers.get("location")).toBe("https://deployconvoy.com/app");
  });

  it("sends a failed exchange back to the public origin", async () => {
    signIn.completeSignIn.mockRejectedValueOnce(new Error("nope"));

    const response = await callback(request(PROXIED, "/api/auth/callback?code=abc"));

    expect(response.headers.get("location")).toBe(
      "https://deployconvoy.com/sign-in?error=auth",
    );
  });

  it("sends a missing code back to the public origin", async () => {
    const response = await callback(request(PROXIED, "/api/auth/callback"));

    expect(response.headers.get("location")).toBe(
      "https://deployconvoy.com/sign-in?error=auth",
    );
    expect(signIn.completeSignIn).not.toHaveBeenCalled();
  });
});

describe("sign-out route", () => {
  afterEach(() => {
    delete process.env.APP_URL;
  });

  it("returns to sign-in on the public origin", async () => {
    const response = await signOut(request(PROXIED, "/api/auth/sign-out"));

    expect(session.destroySession).toHaveBeenCalled();
    expect(response.headers.get("location")).toBe("https://deployconvoy.com/sign-in");
  });
});
