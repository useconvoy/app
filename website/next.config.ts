import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // One traced output runs the landing page, the workspace and the server APIs
  // with `node server.js`; deployment supplies the server-only environment.
  output: "standalone",
  // The landing page canonical is https://deployconvoy.com/. The workspace
  // (/app) is marked noindex and stays out of the public sitemap.
  trailingSlash: true,
  // ...without a normalizing hop: the redirects below already send every
  // legacy path straight to its destination, and static files never carry a slash.
  skipTrailingSlashRedirect: true,
  // Landing and workspace use prepared static image assets. Neither surface
  // needs the image optimizer.
  images: { unoptimized: true },
  // Previous marketing paths keep their landing redirect. The retired console
  // (Applications with its Device, Chat, Usage and Traces views) and its entry
  // points lead to Configurations; a request's query is passed through.
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
      ...LEGACY_CONSOLE_PATHS.map((source) => ({ source, destination: "/app/configurations", permanent: true })),
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

const LEGACY_CONSOLE_PATHS = ["/app/applications", "/app/device", "/portal", "/console"];

export default nextConfig;
