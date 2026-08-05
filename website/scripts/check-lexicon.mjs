#!/usr/bin/env node
/**
 * Lexicon check: internal vocabulary and em dashes must never reach rendered
 * output. Scans string literals and JSX text in src/ for internal nouns
 * (which may only surface through src/lexicon.ts) and for em dashes in any
 * user-facing string. Fails with file:line diagnostics.
 *
 * The glossary route is exempt: it exists to explain internal terms that
 * leak through exports and event payloads.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const SRC = join(ROOT, "src");

const INTERNAL_NOUNS = [
  "agent",
  "agents",
  "sandbox",
  "sandboxes",
  "eval",
  "evals",
  "artifact",
  "artifacts",
  "gate",
  "gates",
  "tenant",
  "tenants",
  "connector",
  "connectors",
  "telemetry",
];

const EXEMPT_PATHS = [
  "src/lexicon.ts",
  "src/app/(portal)/app/glossary/",
];

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, files);
    else if (/\.(ts|tsx)$/.test(entry) && !/\.(test|spec)\.(ts|tsx)$/.test(entry)) files.push(full);
  }
  return files;
}

/** Extract string literals and JSX text with their line numbers. */
function extractRenderableText(source) {
  const found = [];
  const stringPattern = /(["'`])((?:\\.|(?!\1)[^\\\n])*)\1/g;
  const jsxTextPattern = />([^<>{}]+)</g;
  for (const pattern of [stringPattern, jsxTextPattern]) {
    for (const match of source.matchAll(pattern)) {
      const text = match[2] ?? match[1];
      if (!text || !text.trim()) continue;
      const line = source.slice(0, match.index).split("\n").length;
      found.push({ text, line });
    }
  }
  return found;
}

/** Skip strings that are clearly code-facing, not copy. */
function isCodeFacing(text) {
  if (!/[a-z]/i.test(text)) return true;
  // Identifiers, paths, URLs, CSS classes, headers, mime types.
  if (/^[\w./:@#?&=[\]{}%,+*^$|\\-]+$/.test(text)) return true;
  if (/^use /.test(text)) return true; // React directives
  return false;
}

const failures = [];
for (const file of walk(SRC)) {
  const rel = relative(ROOT, file);
  if (EXEMPT_PATHS.some((p) => rel.startsWith(p))) continue;
  const source = readFileSync(file, "utf8");
  for (const { text, line } of extractRenderableText(source)) {
    if (text.includes("—")) {
      failures.push(`${rel}:${line} em dash in string: ${JSON.stringify(text.slice(0, 60))}`);
    }
    if (isCodeFacing(text)) continue;
    for (const noun of INTERNAL_NOUNS) {
      const wordPattern = new RegExp(`(?<![\\w-])${noun}(?![\\w-])`, "i");
      if (wordPattern.test(text)) {
        failures.push(
          `${rel}:${line} internal noun ${JSON.stringify(noun)} in copy: ${JSON.stringify(text.slice(0, 60))}`,
        );
      }
    }
  }
}

if (failures.length > 0) {
  console.error(`lexicon check failed (${failures.length}):`);
  for (const failure of failures) console.error(`  ${failure}`);
  process.exit(1);
}
console.log("lexicon check passed");
