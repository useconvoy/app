/**
 * Unit coverage for the pure validation the org server actions rely on:
 * invite expiry and acceptability, token and tenant id shape, role and
 * capability vocabulary. The actions themselves are exercised against a
 * real database in the integration suite.
 */
import { describe, expect, it } from "vitest";

import {
  CAPABILITIES,
  INVITE_TTL_MS,
  ROLES,
  inviteExpiry,
  inviteVerdict,
  isEmail,
  isInviteToken,
  isRole,
  isTenantId,
  newInviteToken,
  newTenantId,
  normalizeEmail,
  parseCapabilities,
} from "@/lib/orgs/validation";

describe("role validation", () => {
  it("accepts exactly the four roles", () => {
    expect(ROLES).toEqual(["admin", "operator", "member", "viewer"]);
    for (const role of ROLES) expect(isRole(role)).toBe(true);
  });

  it("rejects unknown, cased, and non-string values", () => {
    expect(isRole("owner")).toBe(false);
    expect(isRole("Admin")).toBe(false);
    expect(isRole("")).toBe(false);
    expect(isRole(undefined)).toBe(false);
    expect(isRole(42)).toBe(false);
  });
});

describe("capability validation", () => {
  it("accepts known capabilities and dedupes", () => {
    expect(parseCapabilities(["promoter", "approver", "promoter"])).toEqual([
      "promoter",
      "approver",
    ]);
    expect(parseCapabilities([])).toEqual([]);
    expect(parseCapabilities(CAPABILITIES)).toEqual([...CAPABILITIES]);
  });

  it("ignores empty entries but rejects the list on any unknown value", () => {
    expect(parseCapabilities(["", "approver", "  "])).toEqual(["approver"]);
    expect(parseCapabilities(["approver", "root"])).toBeNull();
    expect(parseCapabilities(["Promoter"])).toBeNull();
  });
});

describe("invite tokens", () => {
  it("are 48 hex chars and unique per call", () => {
    const a = newInviteToken();
    const b = newInviteToken();
    expect(a).toMatch(/^[0-9a-f]{48}$/);
    expect(isInviteToken(a)).toBe(true);
    expect(a).not.toBe(b);
  });

  it("shape check rejects near-misses", () => {
    expect(isInviteToken("")).toBe(false);
    expect(isInviteToken("short")).toBe(false);
    expect(isInviteToken(`${newInviteToken()}0`)).toBe(false);
    expect(isInviteToken(newInviteToken().toUpperCase())).toBe(false);
  });
});

describe("invite expiry", () => {
  it("is exactly seven days out", () => {
    const now = new Date("2026-08-05T12:00:00Z");
    expect(inviteExpiry(now).getTime() - now.getTime()).toBe(INVITE_TTL_MS);
    expect(INVITE_TTL_MS).toBe(7 * 24 * 60 * 60 * 1000);
  });
});

describe("invite verdict", () => {
  const now = new Date("2026-08-05T12:00:00Z");
  const future = new Date(now.getTime() + 1000);
  const past = new Date(now.getTime() - 1000);

  it("pending and unexpired is acceptable", () => {
    expect(inviteVerdict({ status: "pending", expiresAt: future }, now)).toBe("ok");
  });

  it("expiry wins over a stale pending status, including the boundary", () => {
    expect(inviteVerdict({ status: "pending", expiresAt: past }, now)).toBe("expired");
    expect(inviteVerdict({ status: "pending", expiresAt: now }, now)).toBe("expired");
  });

  it("terminal statuses are surfaced as themselves", () => {
    expect(inviteVerdict({ status: "revoked", expiresAt: future }, now)).toBe("revoked");
    expect(inviteVerdict({ status: "accepted", expiresAt: future }, now)).toBe("accepted");
    expect(inviteVerdict({ status: "expired", expiresAt: future }, now)).toBe("invalid");
  });
});

describe("tenant ids", () => {
  it("are prefixed with 12 hex chars of entropy and unique", () => {
    const a = newTenantId();
    expect(a).toMatch(/^tenant-[0-9a-f]{12}$/);
    expect(isTenantId(a)).toBe(true);
    expect(a).not.toBe(newTenantId());
  });

  it("shape check rejects near-misses", () => {
    expect(isTenantId("tenant-")).toBe(false);
    expect(isTenantId("tenant-XYZ")).toBe(false);
    expect(isTenantId("org-abcdefabcdef")).toBe(false);
  });
});

describe("email checks", () => {
  it("accepts ordinary addresses and normalizes case", () => {
    expect(isEmail("j.doe@example.com")).toBe(true);
    expect(normalizeEmail("  J.Doe@Example.COM ")).toBe("j.doe@example.com");
  });

  it("rejects obviously malformed input", () => {
    expect(isEmail("")).toBe(false);
    expect(isEmail("no-at-sign")).toBe(false);
    expect(isEmail("two@@example.com")).toBe(false);
    expect(isEmail("spaces in@example.com")).toBe(false);
    expect(isEmail("nodot@localhost")).toBe(false);
  });
});
