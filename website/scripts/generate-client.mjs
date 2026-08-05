#!/usr/bin/env node
/**
 * Regenerate the typed control-plane client from the checked-in OpenAPI
 * document. With --check, regenerates to a temp path and fails on drift so
 * CI catches an API change whose client was not re-committed.
 *
 * The OpenAPI document itself is exported from the control plane
 * (`uv run python -c "...app.openapi()..."` at the repo root); when uv is
 * available the export runs first so drift in the Python surface is caught
 * too. Without uv (pure Node CI), the committed openapi.json is trusted.
 */
import { execFileSync, execSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;
const REPO_ROOT = join(ROOT, "..");
const SPEC = join(ROOT, "openapi.json");
const OUTPUT = join(ROOT, "src/lib/api/schema.d.ts");
const checkMode = process.argv.includes("--check");

function hasUv() {
  try {
    execSync("uv --version", { stdio: "ignore" });
    return existsSync(join(REPO_ROOT, "agent-runtime"));
  } catch {
    return false;
  }
}

if (hasUv()) {
  const exportScript = [
    "import json",
    "from convoy_runtime.control_plane.app import app",
    `spec = app.openapi()`,
    `print(json.dumps(spec, indent=2, sort_keys=True))`,
  ].join("\n");
  const fresh = execFileSync("uv", ["run", "python", "-c", exportScript], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  });
  const current = existsSync(SPEC) ? readFileSync(SPEC, "utf8") : "";
  if (current.trim() !== fresh.trim()) {
    if (checkMode) {
      console.error("openapi.json is stale: control-plane surface changed. Run npm run generate:client.");
      process.exit(1);
    }
    writeFileSync(SPEC, fresh.trim() + "\n");
    console.log("openapi.json refreshed from the control plane");
  }
}

const generated = execFileSync(
  "node",
  [join(ROOT, "node_modules/openapi-typescript/bin/cli.js"), SPEC],
  { encoding: "utf8" },
);

const current = existsSync(OUTPUT) ? readFileSync(OUTPUT, "utf8") : "";
if (checkMode) {
  if (current !== generated) {
    console.error("generated client is stale. Run npm run generate:client and commit the diff.");
    process.exit(1);
  }
  console.log("generated client is up to date");
} else {
  writeFileSync(OUTPUT, generated);
  console.log(`generated ${OUTPUT.replace(ROOT, ".")}`);
}
