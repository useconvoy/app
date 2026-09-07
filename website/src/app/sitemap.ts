import type { MetadataRoute } from "next";

import { SITE } from "@/content/homepage";

export const dynamic = "force-static";

/** One public route. `lastModified` is the build date; the page is rebuilt on every change. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: `${SITE.url}/`, lastModified: new Date(), changeFrequency: "monthly", priority: 1 }];
}
