import { describe, expect, it } from "vitest";

import { authorizeUrl, signState, verifyState } from "@/lib/connectors/oauth-install";

const SECRET = "test-secret-test-secret-test-secret";
const INPUT = { orgId: "org-1", userId: "user-1", nonce: "nonce-1" };

describe("hosted install state", () => {
  it("round-trips a signed state within its lifetime", () => {
    const state = signState(INPUT, SECRET, 1_000);
    const parsed = verifyState(state, SECRET, 2_000);
    expect(parsed).toMatchObject(INPUT);
  });

  it("rejects tampering, wrong secrets, and expiry", () => {
    const state = signState(INPUT, SECRET, 1_000);
    expect(verifyState(state + "x", SECRET, 2_000)).toBeNull();
    expect(verifyState(state, "other-secret", 2_000)).toBeNull();
    // Past the ten-minute stamp.
    expect(verifyState(state, SECRET, 1_000 + 11 * 60 * 1000)).toBeNull();
    expect(verifyState("not-a-state", SECRET, 2_000)).toBeNull();
  });
});

describe("authorizeUrl", () => {
  it("builds the consent URL from the registry-served directory pieces", () => {
    const url = new URL(
      authorizeUrl(
        {
          authorizeUrl: "https://slack.com/oauth/v2/authorize",
          clientId: "client-1",
          scopes: ["channels:read", "chat:write"],
        },
        {
          redirectUri: "https://example.com/api/connectors/slack/callback",
          state: "state-1",
        },
      ),
    );
    expect(url.origin + url.pathname).toBe("https://slack.com/oauth/v2/authorize");
    expect(url.searchParams.get("client_id")).toBe("client-1");
    expect(url.searchParams.get("scope")).toBe("channels:read,chat:write");
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://example.com/api/connectors/slack/callback",
    );
    expect(url.searchParams.get("state")).toBe("state-1");
  });
});
