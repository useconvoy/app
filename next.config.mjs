/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Self-contained server bundle for the Docker image (docs/deployment.md).
  output: "standalone",
};

export default nextConfig;
