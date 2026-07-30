import {
  randomBytes,
  scryptSync,
  timingSafeEqual,
} from "node:crypto";

const KEY_LENGTH = 64;

export function hashPassword(password: string): string {
  const salt = randomBytes(16);
  const derived = scryptSync(password, salt, KEY_LENGTH);
  return `scrypt:${salt.toString("base64url")}:${derived.toString("base64url")}`;
}

export function verifyPassword(password: string, encoded: string): boolean {
  const [algorithm, saltValue, hashValue] = encoded.split(":");
  if (algorithm !== "scrypt" || !saltValue || !hashValue) return false;
  try {
    const salt = Buffer.from(saltValue, "base64url");
    const expected = Buffer.from(hashValue, "base64url");
    const actual = scryptSync(password, salt, expected.length);
    return expected.length === actual.length && timingSafeEqual(expected, actual);
  } catch {
    return false;
  }
}
