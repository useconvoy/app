import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The SSE proxy and action routes must stream and stay request-scoped;
  // nothing in the app relies on static export.
  reactStrictMode: true,
};

export default nextConfig;
