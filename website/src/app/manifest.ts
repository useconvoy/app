import type { MetadataRoute } from "next";

import { BRAND, SITE } from "@/content/homepage";
import { color } from "@/lib/tokens";

export const dynamic = "force-static";

/**
 * Home-screen icons and colors for browsers that ask. `display: browser`
 * keeps it a bookmark: no install prompt, no app shell, no offline claim.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: SITE.name,
    short_name: SITE.name,
    description: SITE.description,
    start_url: "/",
    display: "browser",
    background_color: color.background,
    theme_color: color.background,
    icons: [
      { src: BRAND.icon192, sizes: "192x192", type: "image/png" },
      { src: BRAND.icon512, sizes: "512x512", type: "image/png" },
    ],
  };
}
