import type { MetadataRoute } from "next";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://shark-app-dj8b4.ondigitalocean.app";

// Only the two public marketing routes are indexable. The workspace pages sit
// on shared, mutating sample state and make poor search entry points; the auth
// and account routes make worse ones.
export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: ["/", "/start"],
      disallow: [
        "/api/",
        "/dashboard",
        "/agents",
        "/environments",
        "/runs",
        "/approvals",
        "/audit",
        "/systems",
        "/missions",
        "/workspaces",
        "/login",
        "/register",
      ],
    },
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}
