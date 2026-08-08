/**
 * Sealing and unsealing customer model keys. This module and store.ts are
 * the only code that ever handles ciphertext; nothing here returns a key to
 * anywhere it should not go, because sealing takes a plaintext in and gives
 * opaque bytes out, and unsealing is reachable only from the store's
 * admin-gated and service-gated paths.
 *
 * The scheme is AES-256-GCM under a 32-byte key-encryption key that lives in
 * the environment, never in the database or the repository. Every seal draws
 * a fresh random 12-byte IV, and the additional-authenticated-data binds the
 * ciphertext to the row it belongs to (org and provider) and the key version
 * that sealed it. A row moved to another org, another provider, or unsealed
 * under a different key version fails its authentication tag and refuses to
 * open rather than yielding a wrong-but-plausible plaintext.
 */
import { createCipheriv, createDecipheriv, createHash, randomBytes } from "node:crypto";

/**
 * The active key-encryption key version. Rotation introduces a new version
 * and re-seals every stored row to it; issuance refuses a row sealed under a
 * version it cannot key, so an un-rotated row fails closed instead of
 * opening under the wrong key.
 */
export const KEK_VERSION = 1;

const IV_BYTES = 12;
const TAG_BYTES = 16;
const KEK_BYTES = 32;

export interface CredentialScope {
  orgId: string;
  provider: string;
}

/**
 * Read and validate the key-encryption key. It arrives as 64 hex characters
 * (32 bytes); anything missing or the wrong length is a misconfiguration we
 * refuse to seal or unseal under, because a weak or absent key would turn
 * sealing into a false promise. Production supplies the real value through
 * the runtime secret; there is no default and no fallback.
 */
function keyEncryptionKey(): Buffer {
  const hex = process.env.CONVOY_KEY_ENCRYPTION_KEY;
  if (!hex) {
    throw new Error("CONVOY_KEY_ENCRYPTION_KEY is not configured");
  }
  if (!/^[0-9a-fA-F]{64}$/.test(hex)) {
    throw new Error("CONVOY_KEY_ENCRYPTION_KEY must be 64 hex characters (32 bytes)");
  }
  const key = Buffer.from(hex, "hex");
  if (key.length !== KEK_BYTES) {
    throw new Error("CONVOY_KEY_ENCRYPTION_KEY must decode to 32 bytes");
  }
  return key;
}

/**
 * The additional-authenticated-data for a row: its org, its provider, and
 * the key version. It is not secret; its job is to make a ciphertext refuse
 * to open anywhere but the row and version it was sealed for.
 */
function scopeAad(scope: CredentialScope): Buffer {
  return Buffer.from(`${scope.orgId}:${scope.provider}:${KEK_VERSION}`, "utf8");
}

/**
 * Seal a plaintext key for one row. Returns iv || authTag || data as a single
 * Buffer ready for the bytea column. Throws before touching the plaintext if
 * the key-encryption key is missing or malformed, so a weak key can never
 * produce a stored ciphertext.
 */
export function seal(plaintext: string, scope: CredentialScope): Buffer {
  const key = keyEncryptionKey();
  const iv = randomBytes(IV_BYTES);
  const cipher = createCipheriv("aes-256-gcm", key, iv);
  cipher.setAAD(scopeAad(scope));
  const data = Buffer.concat([cipher.update(plaintext, "utf8"), cipher.final()]);
  const authTag = cipher.getAuthTag();
  return Buffer.concat([iv, authTag, data]);
}

/**
 * Open a sealed key for one row. The additional-authenticated-data must match
 * the org, provider, and key version the row was sealed under; a tampered
 * byte, a transplanted row, or a wrong scope fails the GCM authentication tag
 * and throws rather than returning anything.
 */
export function unseal(ciphertext: Buffer, scope: CredentialScope): string {
  const key = keyEncryptionKey();
  if (ciphertext.length < IV_BYTES + TAG_BYTES) {
    throw new Error("sealed value is too short to be valid");
  }
  const iv = ciphertext.subarray(0, IV_BYTES);
  const authTag = ciphertext.subarray(IV_BYTES, IV_BYTES + TAG_BYTES);
  const data = ciphertext.subarray(IV_BYTES + TAG_BYTES);
  const decipher = createDecipheriv("aes-256-gcm", key, iv);
  decipher.setAAD(scopeAad(scope));
  decipher.setAuthTag(authTag);
  return Buffer.concat([decipher.update(data), decipher.final()]).toString("utf8");
}

/** A sha256 of the plaintext, hex. Identifies a key without revealing it. */
export function fingerprint(plaintext: string): string {
  return createHash("sha256").update(plaintext, "utf8").digest("hex");
}

/** The last four characters of the key, the one fragment shown to a person. */
export function last4(plaintext: string): string {
  return plaintext.slice(-4);
}
