// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({
  authorizationUrl: vi.fn((redirectUri: string) => {
    const url = new URL("https://auth.example/authorize");
    url.searchParams.set("redirect_uri", redirectUri);
    return url.toString();
  }),
  workosEnabled: vi.fn(() => true),
}));

vi.mock("@/lib/auth/workos", () => auth);

import { NextRequest } from "next/server";

import { GET } from "@/app/api/auth/workos/route";

function request(headers: Record<string, string> = {}): NextRequest {
  return new NextRequest("https://localhost:3000/api/auth/workos", { headers });
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
    const response = GET(
      request({
        "x-forwarded-host": "deployconvoy.com",
        "x-forwarded-proto": "https",
      }),
    );

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

    const response = GET(
      request({
        "x-forwarded-host": "deployconvoy.com",
        "x-forwarded-proto": "https",
      }),
    );

    expect(response.headers.get("location")).toBe("https://deployconvoy.com/sign-in");
    expect(auth.authorizationUrl).not.toHaveBeenCalled();
  });
});
