import "server-only";

import { readFileSync } from "node:fs";
import { join } from "node:path";

/**
 * Token values for the places CSS cannot reach: the theme-color meta tag and
 * the link-preview image, which is rasterised on the server. Reading the
 * stylesheet keeps one source of truth; a missing token fails the build
 * here instead of shipping a wrong color into a preview card.
 */
const SOURCE = readFileSync(join(process.cwd(), "src/styles/tokens.css"), "utf8");

function token(name: string): string {
  const found = SOURCE.match(new RegExp(`--${name}:\\s*(\\S+?);`));
  if (!found?.[1]) throw new Error(`design token --${name} is not defined in tokens.css`);
  return found[1];
}

export const color = {
  background: token("background"),
  surface: token("surface"),
  surfaceSubtle: token("surface-subtle"),
  textPrimary: token("text-primary"),
  textSecondary: token("text-secondary"),
  textMuted: token("text-muted"),
  borderSubtle: token("border-subtle"),
  borderStrong: token("border-strong"),
  accent: token("accent"),
  inverseBackground: token("inverse-background"),
  inverseText: token("inverse-text"),
  inverseMuted: token("inverse-muted"),
} as const;
