import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Design tokens, read from the stylesheet that defines them.
 *
 * Three things need color values that CSS cannot supply: the theme-color meta
 * tag, the web manifest, and the link-preview images, which are rasterised on
 * the server rather than styled in the browser. Rather than transcribing the
 * palette into TypeScript and letting the two copies drift, this parses
 * tokens.css at build time, so renaming or restyling a token still leaves one
 * source of truth. A token that goes missing fails the build here instead of
 * shipping a wrong color into a favicon or an OpenGraph card.
 *
 * Server-only: this reads from disk and must never be imported by a client
 * component.
 */
const SOURCE = readFileSync(
  join(process.cwd(), "src/styles/tokens.css"),
  "utf8",
);

function token(name: string): string {
  const pattern = new RegExp(`--${name}:\\s*(\\S+?);`);
  const found = SOURCE.match(pattern);
  if (!found?.[1]) {
    throw new Error(`design token --${name} is not defined in tokens.css`);
  }
  return found[1];
}

export const color = {
  field: token("color-field"),
  card: token("color-card"),
  cardAlt: token("color-card-alt"),
  ink: token("color-ink"),
  muted: token("color-muted"),
  pine: token("color-pine"),
  line: token("color-line"),
  rule: token("color-rule"),
} as const;
