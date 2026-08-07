#!/usr/bin/env node
/**
 * Token check, two rules.
 *
 * 1. Hex color literals may exist only in src/styles/tokens.css. Everything
 *    else references tokens through CSS variables or the Tailwind theme keys
 *    they feed.
 *
 * 2. Status fills may not be used as text colors. The fill tokens are tuned
 *    for shapes; as glyphs they measure below the 4.5:1 WCAG AA needs, and
 *    the -text variants exist for exactly this. This rule is here because the
 *    portal drifted onto the fills in 26 places before anyone measured them.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SRC = join(ROOT, "src");
const ALLOWED = "src/styles/tokens.css";

const HEX_PATTERN = /#[0-9a-fA-F]{3,8}\b/g;

/* text-hold fails on every surface in the system (3.84:1 on card, 3.59:1 on
 * field). text-pass passes on card and field but not on its own soft fill, so
 * it is caught by the paired rule below rather than banned outright. */
const BANNED_TEXT = [
  {
    pattern: /\btext-hold(?![-\w])/g,
    fix: "text-hold-text",
    why: "3.84:1 on card, 3.59:1 on field",
  },
];

/* A fill used as the text color on its own soft ground. */
const BANNED_PAIRS = [
  {
    pattern: /bg-pass-soft[^"'`]*\btext-pass(?![-\w])/g,
    fix: "text-pass-text",
    why: "4.20:1 on bg-pass-soft",
  },
  {
    pattern: /bg-hold-soft[^"'`]*\btext-hold(?![-\w])/g,
    fix: "text-hold-text",
    why: "3.35:1 on bg-hold-soft",
  },
];

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, files);
    else if (/\.(ts|tsx|css)$/.test(entry)) files.push(full);
  }
  return files;
}

const failures = [];
for (const file of walk(SRC)) {
  const rel = relative(ROOT, file);
  if (rel === ALLOWED) continue;
  const source = readFileSync(file, "utf8");
  const lines = source.split("\n");

  lines.forEach((line, index) => {
    for (const rule of [...BANNED_TEXT, ...BANNED_PAIRS]) {
      for (const match of line.match(rule.pattern) ?? []) {
        const used = match.match(/\btext-[\w-]+$/)?.[0] ?? match;
        failures.push(
          `${rel}:${index + 1} ${used} as a text color (${rule.why}); use ${rule.fix}`,
        );
      }
    }
  });

  lines.forEach((line, index) => {
    // Ignore hex fragments inside URLs and ids; flag color-shaped literals.
    const matches = line.match(HEX_PATTERN);
    if (!matches) return;
    for (const match of matches) {
      if (/^#[0-9a-fA-F]{3}$|^#[0-9a-fA-F]{4}$|^#[0-9a-fA-F]{6}$|^#[0-9a-fA-F]{8}$/.test(match)) {
        failures.push(`${rel}:${index + 1} hex literal ${match} outside tokens.css`);
      }
    }
  });
}

if (failures.length > 0) {
  console.error(`token check failed (${failures.length}):`);
  for (const failure of failures) console.error(`  ${failure}`);
  process.exit(1);
}
console.log("token check passed");
