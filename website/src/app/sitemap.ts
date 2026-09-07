import type { MetadataRoute } from "next";

import { SITE } from "@/content/homepage";

export const dynamic = "force-static";

/** One public route, in its canonical form. `lastModified` is the build date; the page is rebuilt on every change. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [{ url: SITE.canonical, lastModified: new Date(), changeFrequency: "monthly", priority: 1 }];
}
