import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  assertValid,
  validateRunSpec,
  validateWorldSpec,
} from "../contracts/validate.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const runDirectory = path.join(root, "examples", "runs");
const worldDirectory = path.join(root, "worlds", "catalog");
let validated = 0;

for (const filename of await readdir(runDirectory)) {
  if (!filename.endsWith(".json")) continue;
  const spec = JSON.parse(await readFile(path.join(runDirectory, filename), "utf8"));
  assertValid(validateRunSpec(spec), filename);
  validated += 1;
}

for (const entry of await readdir(worldDirectory, { withFileTypes: true })) {
  if (!entry.isDirectory()) continue;
  const manifest = JSON.parse(
    await readFile(path.join(worldDirectory, entry.name, "world.json"), "utf8"),
  );
  assertValid(validateWorldSpec(manifest), `${entry.name}/world.json`);
  if (manifest.metadata.id !== entry.name) {
    throw new Error(
      `${entry.name}/world.json metadata.id must match its directory name.`,
    );
  }
  await readFile(path.join(worldDirectory, entry.name, "initial-state.json"), "utf8");
  await readFile(path.join(worldDirectory, entry.name, "successful-run.json"), "utf8");
  await readFile(
    path.join(worldDirectory, entry.name, manifest.evaluation.module),
    "utf8",
  );
  validated += 1;
}

console.log(`Validated ${validated} Convoy specs.`);
