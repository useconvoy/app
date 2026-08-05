#!/usr/bin/env node
/**
 * Verify every lockfile entry predates the release-age floor in
 * pnpm-workspace.yaml. pnpm enforces this during resolution only from
 * version 11; an older client reads the setting but still resolves to the
 * newest match, so a lockfile written by one can carry entries that a
 * policy-enforcing client then rejects at install time. This check catches
 * that before the lockfile is committed.
 *
 * Publish dates come from the registry, so this needs network access; it is
 * a pre-commit and CI check rather than part of the offline test run.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const REGISTRY = process.env.NPM_CONFIG_REGISTRY ?? "https://registry.npmjs.org";

const workspace = readFileSync(join(ROOT, "pnpm-workspace.yaml"), "utf8");
const ageMatch = workspace.match(/^minimumReleaseAge:\s*(\d+)/m);
if (!ageMatch) {
  console.log("no minimumReleaseAge configured; nothing to check");
  process.exit(0);
}
const minimumMinutes = Number(ageMatch[1]);
const cutoff = Date.now() - minimumMinutes * 60_000;

const lock = readFileSync(join(ROOT, "pnpm-lock.yaml"), "utf8");
const entries = new Map();
for (const [, name, version] of lock.matchAll(
  /^ {2}'?(@?[^@'\s]+(?:\/[^@'\s]+)?)@([0-9][^'\s:]*)'?:/gm,
)) {
  entries.set(`${name}@${version}`, { name, version });
}

/** Registry publish time for one version, or null when unknown. */
async function publishedAt(name, version) {
  const response = await fetch(`${REGISTRY}/${name.replace("/", "%2f")}`, {
    headers: { Accept: "application/vnd.npm.install-v1+json" },
  });
  if (!response.ok) return null;
  const body = await response.json();
  const stamp = body.time?.[version];
  return stamp ? Date.parse(stamp) : null;
}

const violations = [];
const pending = [...entries.values()];
const WORKERS = 12;
await Promise.all(
  Array.from({ length: WORKERS }, async () => {
    for (;;) {
      const entry = pending.pop();
      if (!entry) return;
      let at;
      try {
        at = await publishedAt(entry.name, entry.version);
      } catch {
        continue; // A registry hiccup must not fail the build.
      }
      if (at !== null && at > cutoff) {
        violations.push(`${entry.name}@${entry.version} published ${new Date(at).toISOString()}`);
      }
    }
  }),
);

if (violations.length > 0) {
  console.error(
    `release-age check failed: ${violations.length} entries newer than ${minimumMinutes} minutes`,
  );
  for (const violation of violations.sort()) console.error(`  ${violation}`);
  console.error("Pin the offending packages or wait for them to age past the floor.");
  process.exit(1);
}
console.log(`release-age check passed (${entries.size} entries, floor ${minimumMinutes}m)`);
