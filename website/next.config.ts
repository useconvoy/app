import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // One traced folder that runs with `node server.js`; the Dockerfile copies
  // it. Nothing on the page needs a full node_modules tree at run time.
  output: "standalone",
  // The page is static text, a diagram and one form. Nothing here needs the
  // image optimizer, so it is off rather than left as an unused route.
  images: { unoptimized: true },
  // Paths the previous marketing site served. They now point at the one
  // page; the old authenticated console routes (/app, /sign-in, ...) are
  // deliberately not recreated and fall through to the 404.
  async redirects() {
    return LEGACY_MARKETING_PATHS.map((source) => ({ source, destination: "/", permanent: true }));
  },
};

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
