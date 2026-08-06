import type { MetadataRoute } from "next";

import { color } from "@/lib/tokens";

export const dynamic = "force-static";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Convoy Labs",
    short_name: "Convoy",
    description:
      "Routine work, done carefully. Convoy runs recurring operational work, rehearses it first, and holds for your sign-off.",
    start_url: "/",
    display: "standalone",
    background_color: color.field,
    theme_color: color.pine,
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
      {
        // Art inside the 80% safe circle, background bleeding to every edge,
        // so an aggressive platform crop cannot clip a checkpoint.
        src: "/icon-maskable-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
