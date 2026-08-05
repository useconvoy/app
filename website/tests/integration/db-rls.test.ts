// @vitest-environment node
/**
 * RLS isolation against a real Postgres. Requires WEBSITE_PG_ADMIN_DSN (the
 * migration owner) and uses WEBSITE_PG_DSN for the RLS-bound app role the
 * server itself uses; the suite skips cleanly when no database is
 * provided. Fixture writes happen under each org's own context through the
 * same withOrgContext/withUserContext helpers as production code; only
 * seeding of orgs/users/memberships and cleanup use the owner connection.
 */
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import pg from "pg";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { withOrgContext, withUserContext } from "@/lib/db";
import { newInviteToken, newTenantId } from "@/lib/orgs/validation";

const ADMIN_DSN = process.env.WEBSITE_PG_ADMIN_DSN;
const APP_DSN =
  process.env.WEBSITE_PG_DSN ??
  "postgresql://convoy_website_app:convoy_website_app@localhost:5440/convoy_website";
const WEBSITE_ROOT = new URL("../..", import.meta.url).pathname;

const run = randomUUID().slice(0, 8);
const orgA = randomUUID();
const orgB = randomUUID();
const userA1 = randomUUID();
const userA2 = randomUUID();
const userB1 = randomUUID();

describe.skipIf(!ADMIN_DSN)("RLS isolation (real Postgres)", () => {
  let admin: pg.Client;
  let teamA: string;
  let teamB: string;
  let inviteTokenA: string;
  const syncedEmails: string[] = [];

  beforeAll(async () => {
    execFileSync(process.execPath, ["scripts/migrate.mjs"], {
      cwd: WEBSITE_ROOT,
      env: { ...process.env, WEBSITE_PG_ADMIN_DSN: ADMIN_DSN as string },
      stdio: "pipe",
    });
    // The app pool (src/lib/db) reads this lazily on first checkout.
    process.env.WEBSITE_PG_DSN = APP_DSN;

    admin = new pg.Client({ connectionString: ADMIN_DSN });
    await admin.connect();
    await admin.query(
      `INSERT INTO organizations (id, tenant_id, name) VALUES
        ($1, $3, 'Org A ${run}'), ($2, $4, 'Org B ${run}')`,
      [orgA, orgB, newTenantId(), newTenantId()],
    );
    await admin.query(
      `INSERT INTO users (id, email, name) VALUES
        ($1, 'a1-${run}@example.com', 'A. One'),
        ($2, 'a2-${run}@example.com', 'A. Two'),
        ($3, 'b1-${run}@example.com', 'B. One')`,
      [userA1, userA2, userB1],
    );
    await admin.query(
      `INSERT INTO memberships (org_id, user_id, role) VALUES
        ($1, $3, 'admin'), ($1, $4, 'member'), ($2, $5, 'admin')`,
      [orgA, orgB, userA1, userA2, userB1],
    );
  }, 30_000);

  afterAll(async () => {
    await globalThis.__convoyWebsitePool?.end();
    globalThis.__convoyWebsitePool = undefined;
    if (!admin) return;
    await admin.query("DELETE FROM notifications WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM admin_audit WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query(
      "DELETE FROM team_memberships tm USING teams t WHERE t.id = tm.team_id AND t.org_id = ANY($1)",
      [[orgA, orgB]],
    );
    await admin.query("DELETE FROM teams WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM invites WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM memberships WHERE org_id = ANY($1)", [[orgA, orgB]]);
    await admin.query("DELETE FROM users WHERE id = ANY($1) OR email = ANY($2)", [
      [userA1, userA2, userB1],
      syncedEmails,
    ]);
    await admin.query("DELETE FROM organizations WHERE id = ANY($1)", [[orgA, orgB]]);
    await admin.end();
  });

  it("each org inserts teams under its own context; the other org sees zero rows", async () => {
    teamA = await withOrgContext({ orgId: orgA, userId: userA1 }, async (client) => {
      const { rows } = await client.query<{ id: string }>(
        "INSERT INTO teams (org_id, name) VALUES ($1, $2) RETURNING id",
        [orgA, `Reviewers ${run}`],
      );
      return rows[0]!.id;
    });
    teamB = await withOrgContext({ orgId: orgB, userId: userB1 }, async (client) => {
      const { rows } = await client.query<{ id: string }>(
        "INSERT INTO teams (org_id, name) VALUES ($1, $2) RETURNING id",
        [orgB, `Reviewers ${run}`],
      );
      return rows[0]!.id;
    });

    const seenFromA = await withOrgContext({ orgId: orgA, userId: userA1 }, async (client) => {
      const all = await client.query<{ id: string }>("SELECT id FROM teams");
      const cross = await client.query("SELECT id FROM teams WHERE org_id = $1", [orgB]);
      const direct = await client.query("SELECT id FROM teams WHERE id = $1", [teamB]);
      return { all: all.rows.map((r) => r.id), cross: cross.rowCount, direct: direct.rowCount };
    });
    expect(seenFromA.all).toContain(teamA);
    expect(seenFromA.all).not.toContain(teamB);
    expect(seenFromA.cross).toBe(0);
    expect(seenFromA.direct).toBe(0);

    const seenFromB = await withOrgContext({ orgId: orgB, userId: userB1 }, (client) =>
      client.query("SELECT id FROM teams WHERE org_id = $1 OR id = $2", [orgA, teamA]),
    );
    expect(seenFromB.rowCount).toBe(0);
  });

  it("team seats are only visible through the owning org's context", async () => {
    await withOrgContext({ orgId: orgA, userId: userA1 }, (client) =>
      client.query("INSERT INTO team_memberships (team_id, user_id) VALUES ($1, $2)", [
        teamA,
        userA2,
      ]),
    );
    const fromB = await withOrgContext({ orgId: orgB, userId: userB1 }, (client) =>
      client.query("SELECT * FROM team_memberships WHERE team_id = $1", [teamA]),
    );
    expect(fromB.rowCount).toBe(0);
    const fromA = await withOrgContext({ orgId: orgA, userId: userA1 }, (client) =>
      client.query("SELECT * FROM team_memberships WHERE team_id = $1", [teamA]),
    );
    expect(fromA.rowCount).toBe(1);
  });

  it("each org inserts invites under its own context; the other org sees zero rows", async () => {
    inviteTokenA = newInviteToken();
    await withOrgContext({ orgId: orgA, userId: userA1 }, (client) =>
      client.query(
        `INSERT INTO invites (org_id, email, role, token, expires_at)
         VALUES ($1, $2, 'member', $3, now() + interval '7 days')`,
        [orgA, `invitee-${run}@example.com`, inviteTokenA],
      ),
    );
    await withOrgContext({ orgId: orgB, userId: userB1 }, (client) =>
      client.query(
        `INSERT INTO invites (org_id, email, role, token, expires_at)
         VALUES ($1, $2, 'viewer', $3, now() + interval '7 days')`,
        [orgB, `invitee-${run}@example.com`, newInviteToken()],
      ),
    );
    const fromB = await withOrgContext({ orgId: orgB, userId: userB1 }, (client) =>
      client.query("SELECT id FROM invites WHERE org_id = $1", [orgA]),
    );
    expect(fromB.rowCount).toBe(0);
    const fromA = await withOrgContext({ orgId: orgA, userId: userA1 }, async (client) => {
      const own = await client.query("SELECT org_id FROM invites");
      return own.rows.every((row) => row.org_id === orgA) && own.rowCount === 1;
    });
    expect(fromA).toBe(true);
  });

  it("a context with no org sees no org-scoped rows at all", async () => {
    const counts = await withUserContext(userA1, async (client) => {
      const teams = await client.query("SELECT id FROM teams");
      const invites = await client.query("SELECT id FROM invites");
      return { teams: teams.rowCount, invites: invites.rowCount };
    });
    expect(counts).toEqual({ teams: 0, invites: 0 });
  });

  it("memberships: active-org rows plus your own rows, never the other org's", async () => {
    const fromA = await withOrgContext({ orgId: orgA, userId: userA1 }, async (client) => {
      const { rows } = await client.query<{ org_id: string; user_id: string }>(
        "SELECT org_id, user_id FROM memberships",
      );
      return rows;
    });
    expect(fromA).toHaveLength(2);
    expect(fromA.every((row) => row.org_id === orgA)).toBe(true);

    // Pre-switch, a user sees their own memberships across orgs and nothing else.
    const ownRows = await withUserContext(userB1, async (client) => {
      const { rows } = await client.query<{ org_id: string; user_id: string }>(
        "SELECT org_id, user_id FROM memberships",
      );
      return rows;
    });
    expect(ownRows).toEqual([{ org_id: orgB, user_id: userB1 }]);
  });

  it("organizations: visible through membership or active context only", async () => {
    const visible = await withUserContext(userA2, async (client) => {
      const { rows } = await client.query<{ id: string }>("SELECT id FROM organizations");
      return rows.map((row) => row.id);
    });
    expect(visible).toContain(orgA);
    expect(visible).not.toContain(orgB);
  });

  it("notifications: scoped to the reader's own inbox within the active org", async () => {
    await withOrgContext({ orgId: orgA, userId: userA1 }, (client) =>
      client.query(
        `INSERT INTO notifications (org_id, user_id, event_id, run_id, class, title, cta_url)
         VALUES ($1, $2, $3, 'run-1', 'checkpoint_opened', 'Held for you', '/app/runs/run-1')`,
        [orgA, userA1, `run-1:1:${run}`],
      ),
    );
    const own = await withOrgContext({ orgId: orgA, userId: userA1 }, (client) =>
      client.query("SELECT id FROM notifications"),
    );
    expect(own.rowCount).toBe(1);
    const otherUserSameOrg = await withOrgContext({ orgId: orgA, userId: userA2 }, (client) =>
      client.query("SELECT id FROM notifications"),
    );
    expect(otherUserSameOrg.rowCount).toBe(0);
    const otherOrg = await withOrgContext({ orgId: orgB, userId: userB1 }, (client) =>
      client.query("SELECT id FROM notifications"),
    );
    expect(otherOrg.rowCount).toBe(0);
    // Right user, wrong active org: still nothing.
    const wrongContext = await withOrgContext({ orgId: orgB, userId: userA1 }, (client) =>
      client.query("SELECT id FROM notifications"),
    );
    expect(wrongContext.rowCount).toBe(0);
  });

  it("invites are reachable by token only through the invite-token context", async () => {
    const withoutToken = await withUserContext(userB1, (client) =>
      client.query("SELECT id FROM invites WHERE token = $1", [inviteTokenA]),
    );
    expect(withoutToken.rowCount).toBe(0);

    const withToken = await withUserContext(userB1, async (client) => {
      await client.query("SELECT set_config('app.invite_token', $1, true)", [inviteTokenA]);
      const invite = await client.query<{ org_id: string }>(
        "SELECT org_id FROM invites WHERE token = $1",
        [inviteTokenA],
      );
      const org = await client.query<{ id: string }>("SELECT id FROM organizations WHERE id = $1", [
        orgA,
      ]);
      const others = await client.query("SELECT id FROM invites WHERE token <> $1", [inviteTokenA]);
      return { invite: invite.rows, org: org.rowCount, others: others.rowCount };
    });
    expect(withToken.invite).toEqual([{ org_id: orgA }]);
    expect(withToken.org).toBe(1);
    expect(withToken.others).toBe(0);

    const wrongToken = await withUserContext(userB1, async (client) => {
      await client.query("SELECT set_config('app.invite_token', $1, true)", [newInviteToken()]);
      return client.query("SELECT id FROM invites");
    });
    expect(wrongToken.rowCount).toBe(0);
  });

  it("sync_user_identity upserts by email without any pre-existing context", async () => {
    const email = `sync-${run}@example.com`;
    syncedEmails.push(email);
    const first = await withUserContext("", async (client) => {
      const { rows } = await client.query<{ id: string; name: string }>(
        "SELECT id, name FROM sync_user_identity($1, $2, $3)",
        [email, "S. Ync", null],
      );
      return rows[0]!;
    });
    const second = await withUserContext("", async (client) => {
      const { rows } = await client.query<{ id: string; name: string }>(
        "SELECT id, name FROM sync_user_identity($1, $2, $3)",
        [email.toUpperCase(), "S. Ync Renamed", `workos-${run}`],
      );
      return rows[0]!;
    });
    expect(second.id).toBe(first.id);
    expect(second.name).toBe("S. Ync Renamed");
  });
});
