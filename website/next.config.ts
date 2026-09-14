import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // One traced output runs the landing page, portal UI and server API with
  // `node server.js`; deployment supplies the server-only portal environment.
  output: "standalone",
  // The landing page canonical is https://deployconvoy.com/. The portal
  // declares its own canonical metadata and stays out of the public sitemap.
  trailingSlash: true,
  // ...without a normalizing hop: the redirects below already send every
  // legacy path straight to "/", and static files never carry a slash.
  skipTrailingSlashRedirect: true,
  // Landing and portal use prepared static image assets. Neither surface
  // needs the image optimizer.
  images: { unoptimized: true },
  // Previous marketing paths keep their landing redirect. The new demo uses
  // /portal and /api/portal; the old /app and /sign-in console stays retired.
  async redirects() {
    return [
      // One public host. Caddy serves both names to this app with the
      // original Host header, so the app answers www with a permanent
      // redirect to the canonical apex URL. DNS and TLS are unchanged; if a
      // proxy ever rewrote Host, this rule would simply not match and www
      // would keep serving the page with its apex canonical.
      { source: "/", has: [{ type: "host", value: WWW_HOST }], destination: `${CANONICAL_ORIGIN}/`, permanent: true },
      { source: "/:path+", has: [{ type: "host", value: WWW_HOST }], destination: `${CANONICAL_ORIGIN}/:path+`, permanent: true },
      ...LEGACY_MARKETING_PATHS.map((source) => ({ source, destination: "/", permanent: true })),
    ];
  },
};

const CANONICAL_ORIGIN = "https://deployconvoy.com";
const WWW_HOST = "www.deployconvoy.com";

const LEGACY_MARKETING_PATHS = [
  "/platform",
  "/solutions",
  "/security",
  "/company",
  "/writing",
  "/changelog",
  "/demo",
  "/early-access",
  "/terms",
  "/privacy",
];

export default nextConfig;
