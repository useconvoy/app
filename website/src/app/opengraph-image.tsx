import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

import { SITE } from "@/content/homepage";
import { color } from "@/lib/tokens";

/**
 * The link-preview card: wordmark, the one-line model to release to robot
 * path, the headline, and the development-stage label. No dashboard, no
 * logos, no numbers. ImageResponse does not see next/font, so the faces are
 * read from disk (WOFF, which the renderer accepts) and handed over.
 */
export const alt = `${SITE.name}: ${SITE.ogTitle}`;
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";

const FONT_DIR = join(process.cwd(), "src/assets/fonts");

export default async function OpenGraphImage() {
  const [sansMedium, sansRegular, mono] = await Promise.all([
    readFile(join(FONT_DIR, "ibm-plex-sans-latin-500-normal.woff")),
    readFile(join(FONT_DIR, "ibm-plex-sans-latin-400-normal.woff")),
    readFile(join(FONT_DIR, "ibm-plex-mono-latin-500-normal.woff")),
  ]);

  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: 60,
          background: color.background,
          color: color.textPrimary,
          fontFamily: "Plex Sans",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ fontSize: 40, fontWeight: 500, letterSpacing: -1 }}>{SITE.name}</div>
          <div
            style={{
              display: "flex",
              fontFamily: "Plex Mono",
              fontSize: 22,
              letterSpacing: 1,
              color: color.textSecondary,
              border: `2px solid ${color.borderStrong}`,
              borderRadius: 3,
              padding: "6px 14px",
            }}
          >
            IN DEVELOPMENT
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 18, fontFamily: "Plex Mono", fontSize: 26, color: color.textSecondary }}>
            <span style={{ display: "flex", border: `2px solid ${color.borderStrong}`, borderRadius: 4, padding: "10px 18px" }}>Trained model</span>
            <span>→</span>
            <span style={{ display: "flex", border: `3px solid ${color.textPrimary}`, borderRadius: 4, padding: "10px 18px", background: color.surface, color: color.textPrimary }}>
              Convoy release
            </span>
            <span>→</span>
            <span style={{ display: "flex", border: `2px solid ${color.borderStrong}`, borderRadius: 4, padding: "10px 18px" }}>Robot controller</span>
          </div>
          <div style={{ fontSize: 88, fontWeight: 500, lineHeight: 1.04, letterSpacing: -3, maxWidth: 1000 }}>{SITE.ogTitle}</div>
        </div>

        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 26, color: color.textSecondary }}>
          <span>{SITE.category}</span>
          <span style={{ fontFamily: "Plex Mono" }}>{SITE.domain}</span>
        </div>
      </div>
    ),
    {
      ...size,
      fonts: [
        { name: "Plex Sans", data: sansMedium, weight: 500, style: "normal" },
        { name: "Plex Sans", data: sansRegular, weight: 400, style: "normal" },
        { name: "Plex Mono", data: mono, weight: 500, style: "normal" },
      ],
    },
  );
}
