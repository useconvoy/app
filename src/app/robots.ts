import type { MetadataRoute } from "next";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://shark-app-dj8b4.ondigitalocean.app";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: "*",
      allow: "/",
      disallow: ["/api/", "/dashboard", "/agents", "/environments", "/runs", "/approvals", "/audit", "/systems"],
    },
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}
