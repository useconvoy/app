#!/usr/bin/env node
/**
 * Publish the Documentation Steward to the catalog: the platform-built demo
 * agent that keeps a Google Drive documentation folder consistent with what
 * is currently true in GitHub issues and Slack conversations, holding for
 * human approval before it touches any document.
 *
 * The storefront description doubles as the installed agent's working goal
 * (the runtime's model planner plans from it), so the watched folder,
 * repository, and channel are stamped in here at publish time. Defaults
 * match the connector sandbox fixture, so a rehearsal run works with no
 * real credentials; pass real identifiers when publishing for live use.
 *
 *   node scripts/seed-doc-steward.mjs \
 *     [--org <org name or uuid>] \
 *     [--folder <drive folder id>] [--repo <owner/repo>] [--channel <name>]
 *
 * Requires WEBSITE_PG_ADMIN_DSN. Publishing again updates the entry's
 * storefront in place without bumping the version; the entry stays at v1
 * until the definition meaningfully changes.
 */
import pg from "pg";

const args = process.argv.slice(2);
function flag(name, fallback) {
  const index = args.indexOf(`--${name}`);
  return index >= 0 && args[index + 1] ? args[index + 1] : fallback;
}

const FOLDER = flag("folder", "folder-shared");
const REPO = flag("repo", "sandbox/example-service");
const CHANNEL = flag("channel", "operations");
const ORG = flag("org", "");

const NAME = "Documentation Steward";
const TAGLINE = "Keeps your Drive docs consistent with GitHub issues and Slack decisions.";
const VERSION_REF = "doc-steward@v1";

const GOAL = `Keep the documentation in the watched Google Drive folder consistent with what is currently true in the team's GitHub issues and Slack conversations.

Watched sources: Drive folder ${FOLDER} (Google Docs), GitHub repository ${REPO}, Slack channel #${CHANNEL}.

Working method: list the documents in the folder and read each one. Read the repository's issues and the recent channel messages. For each document, find statements the issues or messages contradict or make outdated, and note decisions no document covers yet. Draft a complete revised version of each document that needs changing: keep its structure, change only what the evidence supports, and record which issue or message drove each change. Before updating any document, stop for human approval with the full list of proposed changes and the reason for each. Apply only approved updates, then post a short summary to the channel naming each changed document and why it changed. If nothing is stale, change nothing and post that the documentation is current.`;

const REQUIREMENTS = {
  systems: [
    { systemId: "document_store", scope: "write" },
    { systemId: "messaging", scope: "write" },
    { systemId: "code_host", scope: "read" },
  ],
  vendorSpecificTools: [],
};

const dsn = process.env.WEBSITE_PG_ADMIN_DSN;
if (!dsn) {
  console.error("WEBSITE_PG_ADMIN_DSN is required");
  process.exit(1);
}

const client = new pg.Client({ connectionString: dsn });
await client.connect();
try {
  const orgRow = ORG
    ? await client.query(
        "SELECT id, name FROM organizations WHERE id::text = $1 OR name = $1",
        [ORG],
      )
    : await client.query("SELECT id, name FROM organizations ORDER BY created_at LIMIT 1");
  const org = orgRow.rows[0];
  if (!org) {
    console.error(ORG ? `no organization matches ${ORG}` : "no organizations exist yet");
    process.exit(1);
  }

  const storefront = JSON.stringify({ name: NAME, tagline: TAGLINE, description: GOAL });
  const requirements = JSON.stringify(REQUIREMENTS);
  const thresholds = JSON.stringify({ minScore: 0 });
  const existing = await client.query(
    `SELECT id FROM catalog_entries
      WHERE split_part(routine_version_ref, '@', 1) = 'doc-steward'`,
  );
  if (existing.rows[0]) {
    await client.query(
      `UPDATE catalog_entries
          SET storefront = $2, capability_requirements = $3, eval_thresholds = $4
        WHERE id = $1`,
      [existing.rows[0].id, storefront, requirements, thresholds],
    );
    console.log(`updated catalog entry ${existing.rows[0].id} (${VERSION_REF})`);
  } else {
    const inserted = await client.query(
      `INSERT INTO catalog_entries
         (publisher_org_id, routine_version_ref, capability_requirements,
          eval_thresholds, visibility, storefront, changelog)
       VALUES ($1, $2, $3, $4, 'convoy', $5, $6)
       RETURNING id`,
      [
        org.id,
        VERSION_REF,
        requirements,
        thresholds,
        storefront,
        JSON.stringify([
          { version: 1, note: "First publish", at: new Date().toISOString() },
        ]),
      ],
    );
    console.log(
      `published ${NAME} as catalog entry ${inserted.rows[0].id} (publisher: ${org.name})`,
    );
  }
  console.log(`watched sources: folder ${FOLDER}, repo ${REPO}, channel #${CHANNEL}`);
  console.log(
    "install it from /app/catalog into a workspace granting Google Drive (write), Slack (write), GitHub (read)",
  );
} finally {
  await client.end();
}
