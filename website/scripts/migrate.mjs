#!/usr/bin/env node
/**
 * Apply db/migrations in filename order, recording applied versions in
 * schema_migrations. Runs as the database owner (WEBSITE_PG_ADMIN_DSN), not
 * the RLS-bound app role the server uses at runtime.
 */
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import pg from "pg";

const ROOT = new URL("..", import.meta.url).pathname;
const MIGRATIONS = join(ROOT, "db/migrations");
const dsn = process.env.WEBSITE_PG_ADMIN_DSN;
if (!dsn) {
  console.error("WEBSITE_PG_ADMIN_DSN is required");
  process.exit(1);
}

const client = new pg.Client({ connectionString: dsn });
await client.connect();
try {
  await client.query(
    "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())",
  );
  const { rows } = await client.query("SELECT version FROM schema_migrations");
  const applied = new Set(rows.map((row) => row.version));
  const files = readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort();
  for (const file of files) {
    if (applied.has(file)) continue;
    const sql = readFileSync(join(MIGRATIONS, file), "utf8");
    await client.query("BEGIN");
    try {
      await client.query(sql);
      await client.query("INSERT INTO schema_migrations (version) VALUES ($1)", [file]);
      await client.query("COMMIT");
      console.log(`applied ${file}`);
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    }
  }
  console.log("migrations up to date");
} finally {
  await client.end();
}
