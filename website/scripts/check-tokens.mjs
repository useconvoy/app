#!/usr/bin/env node
/**
 * Token check: hex color literals may exist only in src/styles/tokens.css.
 * Everything else references tokens through CSS variables or the Tailwind
 * theme keys they feed.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SRC = join(ROOT, "src");
const ALLOWED = "src/styles/tokens.css";

const HEX_PATTERN = /#[0-9a-fA-F]{3,8}\b/g;

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
