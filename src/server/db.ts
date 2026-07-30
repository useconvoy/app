import fs from "node:fs";
import path from "node:path";
import type { Database } from "./types";
import { seedDatabase } from "./seed";

// JSON-file persistence keeps the demo zero-infra (no Postgres to stand up on
// demo day) while preserving the exact table shapes from the spec (§6.3).
// The production seam is swapping this module for Postgres — nothing above it
// (gateway, runtime, harness, UI) knows the difference.

// Overridable so containers can mount a persistent volume (docs/deployment.md).
const DATA_DIR = process.env.CONVOY_DATA_DIR ?? path.join(process.cwd(), ".data");
const DATA_FILE = path.join(DATA_DIR, "db.json");

const g = globalThis as unknown as { __convoyDb?: Database; __convoySaveTimer?: NodeJS.Timeout };

export function db(): Database {
  if (g.__convoyDb) return g.__convoyDb;
  if (fs.existsSync(DATA_FILE)) {
    g.__convoyDb = JSON.parse(fs.readFileSync(DATA_FILE, "utf-8")) as Database;
  } else {
    g.__convoyDb = seedDatabase();
    persistNow();
  }
  return g.__convoyDb;
}

export function persist(): void {
  // Debounced write — many trace rows can land in one tick.
  if (g.__convoySaveTimer) return;
  g.__convoySaveTimer = setTimeout(() => {
    g.__convoySaveTimer = undefined;
    persistNow();
  }, 150);
}

function persistNow(): void {
  if (!g.__convoyDb) return;
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const tmp = DATA_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(g.__convoyDb));
  fs.renameSync(tmp, DATA_FILE);
}

export function resetDatabase(): Database {
  g.__convoyDb = seedDatabase();
  persistNow();
  return g.__convoyDb;
}
