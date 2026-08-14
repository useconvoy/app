import { describe, expect, it } from "vitest";

import {
  SLACK_SCOPES,
  signState,
  slackAuthorizeUrl,
  verifyState,
} from "@/lib/connectors/slack-oauth";

const SECRET = "test-secret-test-secret-test-secret";
const INPUT = { orgId: "org-1", userId: "user-1", nonce: "nonce-1" };

describe("slack oauth state", () => {
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

describe("slackAuthorizeUrl", () => {
  it("targets Slack's consent screen with exactly the connector's scopes", () => {
    const url = new URL(
      slackAuthorizeUrl({
        clientId: "client-1",
        redirectUri: "https://example.com/api/connectors/slack/callback",
        state: "state-1",
      }),
    );
    expect(url.origin + url.pathname).toBe("https://slack.com/oauth/v2/authorize");
    expect(url.searchParams.get("client_id")).toBe("client-1");
    expect(url.searchParams.get("scope")).toBe(SLACK_SCOPES.join(","));
    expect(url.searchParams.get("redirect_uri")).toBe(
      "https://example.com/api/connectors/slack/callback",
    );
    expect(url.searchParams.get("state")).toBe("state-1");
  });
});
