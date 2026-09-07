#!/usr/bin/env node
/**
 * Semantic tokens stay centralized: a hex color literal may appear only in
 * src/styles/tokens.css. Everything else reaches color through the CSS
 * variables (or the Tailwind theme keys that alias them). Fails with
 * file:line diagnostics.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SRC = join(ROOT, "src");
const ALLOWED = "src/styles/tokens.css";
// The favicon cannot read CSS variables; it copies two token values and says so.
const EXEMPT = new Set(["src/app/icon.svg"]);
const HEX = /#[0-9a-fA-F]{3,8}\b/g;

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, files);
    else if (/\.(ts|tsx|css|mjs|svg)$/.test(entry)) files.push(full);
  }
  return files;
}

const failures = [];
for (const file of walk(SRC)) {
  const rel = relative(ROOT, file);
  if (rel === ALLOWED || EXEMPT.has(rel)) continue;
  const lines = readFileSync(file, "utf8").split("\n");
  lines.forEach((line, index) => {
    // Anchors like href="#contact" are not colors.
    const stripped = line.replace(/href="#[^"]*"/g, "").replace(/url\(#[^)]*\)/g, "");
    for (const match of stripped.matchAll(HEX)) {
      failures.push(`${rel}:${index + 1} hex literal ${match[0]} (only ${ALLOWED} may carry hex)`);
    }
  });
}

if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("check:tokens ok");
