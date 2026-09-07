import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // One traced folder that runs with `node server.js`; the Dockerfile copies
  // it. Nothing on the page needs a full node_modules tree at run time.
  output: "standalone",
  // The canonical form of the one page is https://deployconvoy.com/ with the
  // slash; this keeps every generated URL (canonical, og:url, redirects) in
  // that form so they match the sitemap byte for byte.
  trailingSlash: true,
  // ...without a normalizing hop: the redirects below already send every
  // legacy path straight to "/", and static files never carry a slash.
  skipTrailingSlashRedirect: true,
  // The page is static text, a diagram and one form. Nothing here needs the
  // image optimizer, so it is off rather than left as an unused route.
  images: { unoptimized: true },
  // Paths the previous marketing site served. They now point at the one
  // page; the old authenticated console routes (/app, /sign-in, ...) are
  // deliberately not recreated and fall through to the 404.
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
