import type { MetadataRoute } from "next";

export const dynamic = "force-static";

const SITE = "https://deployconvoy.com";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Everything behind sign-in is per-organization and row-level
        // restricted; none of it should ever reach an index.
        disallow: ["/app/", "/sign-in", "/onboarding", "/switch", "/invite/", "/api/"],
      },
    ],
    sitemap: `${SITE}/sitemap.xml`,
    host: SITE,
  };
}
