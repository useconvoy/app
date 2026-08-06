import { readFile } from "node:fs/promises";
import { join } from "node:path";

import { ImageResponse } from "next/og";

import { color } from "@/lib/tokens";

/**
 * The shared link-preview card.
 *
 * This buyer does not share on X. They paste links into Slack, Teams, and
 * email, where a director decides in about three seconds whether the sender
 * looks like a real company. So the card is built from the same system as the
 * site rather than a stock gradient, and everything sits inside a 60px safe
 * margin because platform crops are aggressive and differ from each other.
 *
 * ImageResponse does not see next/font, so the two faces are read off disk and
 * handed over as buffers.
 */
export const OG_SIZE = { width: 1200, height: 630 };
export const OG_CONTENT_TYPE = "image/png";

const FONT_DIR = join(process.cwd(), "src/assets/fonts");

async function fonts() {
  const [besley, mono] = await Promise.all([
    readFile(join(FONT_DIR, "besley-500.ttf")),
    readFile(join(FONT_DIR, "spline-mono-600.ttf")),
  ]);
  const besleyBold = await readFile(join(FONT_DIR, "besley-800.ttf"));
  return [
    { name: "Besley", data: besley, weight: 500 as const, style: "normal" as const },
    { name: "Besley", data: besleyBold, weight: 800 as const, style: "normal" as const },
    { name: "Mono", data: mono, weight: 600 as const, style: "normal" as const },
  ];
}

function RouteMark() {
  return (
    <svg width="58" height="27" viewBox="0 0 30 14" fill="none">
      <line x1="1" y1="7" x2="29" y2="7" stroke={color.pine} strokeWidth="2" />
      <circle cx="5" cy="7" r="3.4" fill={color.pine} />
      <circle cx="15" cy="7" r="3.4" fill={color.field} stroke={color.pine} strokeWidth="2" />
      <circle cx="25" cy="7" r="3.4" fill={color.field} stroke={color.pine} strokeWidth="2" />
    </svg>
  );
}

/**
 * @param eyebrow names the page
 * @param title up to two lines; longer titles are the caller's problem to trim
 */
export async function ogCard(eyebrow: string, title: string[]) {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          background: color.field,
          padding: 60,
          fontFamily: "Besley",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
          <RouteMark />
          <span
            style={{
              fontSize: 34,
              fontWeight: 800,
              letterSpacing: "0.06em",
              color: color.ink,
            }}
          >
            CONVOY LABS
          </span>
        </div>

        {/* Anchored to the bottom rather than floated, so the card reads as a
            composed page instead of a caption adrift in empty paper. */}
        <div style={{ display: "flex", flexDirection: "column" }}>
          <span
            style={{
              fontFamily: "Mono",
              fontSize: 20,
              fontWeight: 600,
              letterSpacing: "0.17em",
              textTransform: "uppercase",
              color: color.muted,
              marginBottom: 26,
            }}
          >
            {eyebrow}
          </span>
          {title.slice(0, 2).map((line) => (
            <span
              key={line}
              style={{ fontSize: 74, lineHeight: 1.15, color: color.ink }}
            >
              {line}
            </span>
          ))}
          <div
            style={{
              height: 2,
              background: color.rule,
              marginTop: 28,
              marginBottom: 24,
            }}
          />
          <span
            style={{
              fontFamily: "Mono",
              fontSize: 20,
              fontWeight: 600,
              letterSpacing: "0.06em",
              color: color.muted,
            }}
          >
            DEPLOYCONVOY.COM
          </span>
        </div>
      </div>
    ),
    { ...OG_SIZE, fonts: await fonts() },
  );
}
