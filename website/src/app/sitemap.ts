import { execSync } from "node:child_process";

import type { MetadataRoute } from "next";

const SITE = "https://deployconvoy.com";

export const dynamic = "force-static";

/** Public marketing routes, with how much they matter relative to each other. */
const ROUTES: { path: string; priority: number; changeFrequency: "weekly" | "monthly" | "yearly" }[] =
  [
    { path: "/", priority: 1, changeFrequency: "weekly" },
    { path: "/platform", priority: 0.9, changeFrequency: "monthly" },
    { path: "/solutions", priority: 0.9, changeFrequency: "monthly" },
    { path: "/security", priority: 0.8, changeFrequency: "monthly" },
    { path: "/demo", priority: 0.8, changeFrequency: "monthly" },
    { path: "/company", priority: 0.6, changeFrequency: "monthly" },
    { path: "/writing", priority: 0.6, changeFrequency: "weekly" },
    { path: "/changelog", priority: 0.5, changeFrequency: "weekly" },
    { path: "/terms", priority: 0.3, changeFrequency: "yearly" },
    { path: "/privacy", priority: 0.3, changeFrequency: "yearly" },
  ];

/**
 * `lastModified` has to be honest or it is worse than absent: a sitemap that
 * claims every page changed today teaches a crawler to ignore the field. The
 * date comes from the last commit that touched the route's source, falling
 * back to the build date only when git is unavailable, as it is in a container
 * built from a copied tree.
 */
function lastModified(path: string): Date {
  const file =
    path === "/"
      ? "src/app/(marketing)/page.tsx"
      : `src/app/(marketing)${path}/page.tsx`;
  try {
    const stamp = execSync(`git log -1 --format=%cI -- ${file}`, {
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (stamp) return new Date(stamp);
  } catch {
    // Not a git checkout; fall through.
  }
  return new Date();
}

export default function sitemap(): MetadataRoute.Sitemap {
  return ROUTES.map(({ path, priority, changeFrequency }) => ({
    url: `${SITE}${path}`,
    lastModified: lastModified(path),
    changeFrequency,
    priority,
  }));
}
