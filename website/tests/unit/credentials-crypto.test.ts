/**
 * Sealing and unsealing customer model keys. These exercise the guarantees
 * the whole feature rests on: a sealed key opens only under its own scope and
 * key, a tampered or transplanted ciphertext refuses to open, and a weak or
 * absent key-encryption key stops sealing before it starts.
 */
import { createHash } from "node:crypto";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { fingerprint, last4, seal, unseal } from "@/lib/credentials/crypto";

const VALID_KEK = "ab".repeat(32); // 64 hex chars == 32 bytes
const KEY = "sk-ant-abcdef0123456789";
const SCOPE = { orgId: "11111111-1111-1111-1111-111111111111", provider: "anthropic" };

beforeEach(() => {
  process.env.CONVOY_KEY_ENCRYPTION_KEY = VALID_KEK;
});
afterEach(() => {
  process.env.CONVOY_KEY_ENCRYPTION_KEY = VALID_KEK;
});

describe("seal / unseal", () => {
  it("round-trips a key under its own scope", () => {
    const sealed = seal(KEY, SCOPE);
    expect(Buffer.isBuffer(sealed)).toBe(true);
    // Layout is iv (12) || authTag (16) || data; data is at least the key.
    expect(sealed.length).toBeGreaterThan(12 + 16);
    expect(unseal(sealed, SCOPE)).toBe(KEY);
  });

  it("produces a different ciphertext each time (random iv)", () => {
    const a = seal(KEY, SCOPE);
    const b = seal(KEY, SCOPE);
    expect(a.equals(b)).toBe(false);
    expect(unseal(a, SCOPE)).toBe(KEY);
    expect(unseal(b, SCOPE)).toBe(KEY);
  });

  it("throws when a ciphertext byte is tampered (GCM authentication)", () => {
    const sealed = seal(KEY, SCOPE);
    const tampered = Buffer.from(sealed);
    const lastIndex = tampered.length - 1;
    tampered[lastIndex] = tampered.readUInt8(lastIndex) ^ 0x01;
    expect(() => unseal(tampered, SCOPE)).toThrow();
  });

  it("throws when unsealed under a different org (wrong AAD)", () => {
    const sealed = seal(KEY, SCOPE);
    const otherOrg = { orgId: "22222222-2222-2222-2222-222222222222", provider: "anthropic" };
    expect(() => unseal(sealed, otherOrg)).toThrow();
  });

  it("throws when unsealed under a different provider (wrong AAD)", () => {
    const sealed = seal(KEY, SCOPE);
    expect(() => unseal(sealed, { ...SCOPE, provider: "openai" })).toThrow();
  });
});

describe("key-encryption key handling (fail closed)", () => {
  it("throws at seal when the key is missing", () => {
    delete process.env.CONVOY_KEY_ENCRYPTION_KEY;
    expect(() => seal(KEY, SCOPE)).toThrow();
  });

  it("throws at seal when the key is the wrong length", () => {
    process.env.CONVOY_KEY_ENCRYPTION_KEY = "abcd"; // far short of 32 bytes
    expect(() => seal(KEY, SCOPE)).toThrow();
  });

  it("throws at unseal when the key is missing", () => {
    const sealed = seal(KEY, SCOPE);
    delete process.env.CONVOY_KEY_ENCRYPTION_KEY;
    expect(() => unseal(sealed, SCOPE)).toThrow();
  });
});

describe("fingerprint / last4", () => {
  it("fingerprint is the sha256 hex of the plaintext, and stable", () => {
    const expected = createHash("sha256").update(KEY, "utf8").digest("hex");
    expect(fingerprint(KEY)).toBe(expected);
    expect(fingerprint(KEY)).toBe(fingerprint(KEY));
    expect(fingerprint(KEY)).not.toBe(fingerprint(KEY + "x"));
  });

  it("last4 is the final four characters", () => {
    expect(last4(KEY)).toBe("6789");
    expect(last4("sk-1234")).toBe("1234");
  });
});
