import { describe, expect, it } from "vitest";

import {
  emailAllowedByEntries,
  entryAdmits,
  parseAllowedEntries,
} from "@/lib/auth/allowlist";

describe("parseAllowedEntries", () => {
  it("splits on commas, trims, lowercases, and drops empties", () => {
    expect(parseAllowedEntries(" A@b.com , @Acme.com ,, ")).toEqual(["a@b.com", "@acme.com"]);
    expect(parseAllowedEntries(undefined)).toEqual([]);
    expect(parseAllowedEntries("")).toEqual([]);
  });
});

describe("entryAdmits", () => {
  it("matches a full email exactly, case-insensitively", () => {
    expect(entryAdmits("Aneesh@Convoy.com", "aneesh@convoy.com")).toBe(true);
    expect(entryAdmits("other@convoy.com", "aneesh@convoy.com")).toBe(false);
  });

  it("matches a domain entry by suffix", () => {
    expect(entryAdmits("anyone@convoy.com", "@convoy.com")).toBe(true);
    expect(entryAdmits("anyone@notconvoy.com", "@convoy.com")).toBe(false);
    // A domain entry must match the whole domain, not a substring of it.
    expect(entryAdmits("anyone@evilconvoy.com", "@convoy.com")).toBe(false);
  });
});

describe("emailAllowedByEntries", () => {
  const entries = parseAllowedEntries("founder@convoylabs.dev,@partner.com");

  it("admits listed emails and listed domains only", () => {
    expect(emailAllowedByEntries("founder@convoylabs.dev", entries)).toBe(true);
    expect(emailAllowedByEntries("someone@partner.com", entries)).toBe(true);
    expect(emailAllowedByEntries("stranger@elsewhere.com", entries)).toBe(false);
    expect(emailAllowedByEntries("", entries)).toBe(false);
  });
});
