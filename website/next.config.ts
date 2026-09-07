import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // One traced folder that runs with `node server.js`; the Dockerfile copies
  // it. Nothing on the page needs a full node_modules tree at run time.
  output: "standalone",
  // The page is static text, a diagram and one form. Nothing here needs the
  // image optimizer, so it is off rather than left as an unused route.
  images: { unoptimized: true },
};

export default nextConfig;
