import type { NextRequest } from "next/server";

/**
 * The origin the browser actually reached this app on.
 *
 * Behind Caddy the container only ever sees its own address, so
 * `request.url` is `https://localhost:3000` and every absolute URL built
 * from it points somewhere the browser cannot follow. WorkOS rejects that as
 * a redirect URI, which is how the problem was found, but the same value is
 * equally wrong for the redirect after a successful sign-in and for the one
 * after sign-out. Anything that leaves this process as a Location header or
 * a redirect_uri has to be built from here.
 *
 * APP_URL is the answer wherever it is set, and it is the only one that is
 * stable: it pins a single origin no matter which hostname a request
 * arrived on, so a user who came in via www does not get sent back to an
 * origin WorkOS has never heard of.
 *
 * The forwarding headers are a fallback for a deployment stamped before
 * APP_URL existed. They are safe to read only because the Caddyfile
 * configures no `trusted_proxies`, which makes Caddy overwrite both headers
 * with the values of the request it actually served rather than passing a
 * client's own through. That is a property of the proxy in front of this
 * app, not of this code: delete the fallback once every instance carries
 * APP_URL rather than relying on it indefinitely.
 */
export function publicOrigin(request: NextRequest): string {
  const configured = process.env.APP_URL?.trim();
  if (configured) {
    try {
      return new URL(configured).origin;
    } catch {
      // A malformed APP_URL is an operator error. Refusing to serve the auth
      // routes over it would lock everyone out for a typo, so carry on to the
      // fallback, which reaches the right answer on a proxied deployment
      // anyway. Say so, because a silent fallback is how a misconfiguration
      // survives to the next person.
      console.error(`APP_URL is not a valid URL: ${configured}`);
    }
  }

  // Caddy normalizes these to the request it served. Only the first value is
  // meaningful; a list means something upstream appended to it.
  const forwardedHost = request.headers
    .get("x-forwarded-host")
    ?.split(",", 1)[0]
    ?.trim();
  const forwardedProto = request.headers
    .get("x-forwarded-proto")
    ?.split(",", 1)[0]
    ?.trim();
  if (forwardedHost && (forwardedProto === "http" || forwardedProto === "https")) {
    try {
      return new URL(`${forwardedProto}://${forwardedHost}`).origin;
    } catch {
      // Ignore malformed proxy metadata and fall back to Next.js's origin.
    }
  }

  return request.nextUrl.origin;
}
