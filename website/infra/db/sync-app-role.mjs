#!/usr/bin/env node
/**
 * Align the RLS-bound role's password with the one Terraform generated.
 *
 * db/migrations/0001_init.sql creates convoy_website_app with a fixed
 * development password so a laptop stack works out of the box. A deployed
 * console must not run on that password, and migrations cannot know the
 * generated one, so the deploy pipeline runs this immediately after
 * migrations: same image, same one-off task, admin DSN and the generated
 * password both injected from Secrets Manager. The value only ever exists
 * inside the task's environment — it is never an argument, never logged.
 *
 * Idempotent: ALTER ROLE to the same password is a no-op, so re-running
 * migrations re-runs this safely.
 */
import pg from "pg";

const ROLE = "convoy_website_app";

const dsn = process.env.WEBSITE_PG_ADMIN_DSN;
if (!dsn) {
  console.error("WEBSITE_PG_ADMIN_DSN is required");
  process.exit(1);
}

const password = process.env.WEBSITE_PG_APP_PASSWORD;
if (!password) {
  console.error("WEBSITE_PG_APP_PASSWORD is required");
  process.exit(1);
}

const client = new pg.Client({ connectionString: dsn });
await client.connect();
try {
  // The password rides a transaction-local GUC and is quoted by format(%L),
  // so it is never string-concatenated into SQL and never appears in a
  // statement the server logs verbatim. ALTER ROLE cannot take a bind
  // parameter, which is why the DO block reads the setting instead.
  await client.query("BEGIN");
  await client.query("SELECT set_config('convoy.app_role_password', $1, true)", [password]);
  await client.query(
    `DO $$
     BEGIN
       EXECUTE format('ALTER ROLE %I WITH LOGIN PASSWORD %L',
                      '${ROLE}', current_setting('convoy.app_role_password'));
     END
     $$`,
  );
  await client.query("COMMIT");
  console.log(`aligned password for role ${ROLE}`);
} finally {
  await client.end();
}
