/**
 * Verifying a model key against its provider. One server-side request to the
 * cheapest authenticated read each provider offers (listing models), so a key
 * that cannot authenticate is caught before it is ever sealed, and a stored
 * key can be re-checked without being shown.
 *
 * The key and the provider's raw response are never logged. The result is a
 * small status code, mapped to plain sentences by the admin copy; the network
 * never sees anything but the key it is meant to authenticate, and this
 * module returns nothing that could reveal it.
 */
import "server-only";

/** Provider identifiers, code-facing; the display names live in the lexicon. */
export type Provider = "anthropic" | "openai";

export const PROVIDERS: readonly Provider[] = ["anthropic", "openai"] as const;

export function isProvider(value: unknown): value is Provider {
  return typeof value === "string" && (PROVIDERS as readonly string[]).includes(value);
}

/**
 * Verification outcomes, code-facing. "ok" only on a clean authenticated
 * read; "rejected" when the provider refuses the key; "unreachable" when the
 * request cannot be completed; "malformed" when the key fails its shape check
 * before any request is made.
 */
export type VerifyStatus = "ok" | "rejected" | "unreachable" | "malformed";

/** ok is true only alongside status "ok"; a failure carries a failure code. */
export type VerifyResult =
  | { ok: true; status: "ok" }
  | { ok: false; status: Exclude<VerifyStatus, "ok"> };

/** The prefix each provider's keys carry, checked before any network call. */
const KEY_PREFIX: Record<Provider, string> = {
  anthropic: "sk-ant-",
  openai: "sk-",
};

/** The cheapest authenticated GET each provider exposes. */
const MODELS_URL: Record<Provider, string> = {
  anthropic: "https://api.anthropic.com/v1/models",
  openai: "https://api.openai.com/v1/models",
};

const TIMEOUT_MS = 10_000;

/** A key that is obviously the wrong shape is rejected without a request. */
export function hasKeyShape(provider: Provider, key: string): boolean {
  const trimmed = key.trim();
  if (trimmed.length < 12) return false;
  return trimmed.startsWith(KEY_PREFIX[provider]);
}

function authHeaders(provider: Provider, key: string): Record<string, string> {
  if (provider === "anthropic") {
    return { "x-api-key": key, "anthropic-version": "2023-06-01" };
  }
  return { Authorization: `Bearer ${key}` };
}

/**
 * Make one authenticated read against the provider. Returns a status code and
 * never the key or the provider's body. A timeout or a transport failure is
 * "unreachable", a refusal is "rejected", and only a 200 is "ok".
 */
export async function verifyKey(provider: Provider, key: string): Promise<VerifyResult> {
  const trimmed = key.trim();
  if (!hasKeyShape(provider, trimmed)) {
    return { ok: false, status: "malformed" };
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const response = await fetch(MODELS_URL[provider], {
      method: "GET",
      headers: authHeaders(provider, trimmed),
      signal: controller.signal,
      cache: "no-store",
    });
    if (response.status === 200) {
      return { ok: true, status: "ok" };
    }
    // An authentication failure is a rejection of the key; anything else
    // (rate limit, provider outage) is treated as not-reachable so the admin
    // is told to try again rather than that their key is bad.
    if (response.status === 401 || response.status === 403 || response.status === 400) {
      return { ok: false, status: "rejected" };
    }
    return { ok: false, status: "unreachable" };
  } catch {
    // Never surface the underlying error: it can carry request details we do
    // not want in a log or a message.
    return { ok: false, status: "unreachable" };
  } finally {
    clearTimeout(timer);
  }
}
