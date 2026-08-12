/**
 * The notifier worker entry point (`npm run notifier`). An interval loop
 * over every organization: consumer tick (feed -> rules -> routing ->
 * exactly-once notification writes) then sweep tick (open_gates deadline
 * notices).
 *
 * Org discovery uses the 0003 list_organizations() system seam (id +
 * tenant id only); all real work then runs inside withOrgContext so RLS
 * bounds every read and write to the org being processed.
 *
 * Logs are structured single lines with counts only: no titles, no
 * prompts, no payload bodies, no secrets.
 */
import { withUserContext } from "../lib/db";
import { consumerTick } from "./consumer";
import { FanInRunFeed } from "./feed";
import { sweepTick } from "./sweep";

const intervalMs = Number(process.env.NOTIFIER_INTERVAL_MS ?? "2000") || 2000;

let stopping = false;

/** Persistent per-org feeds so fan-in per-run cursors survive across ticks. */
const feeds = new Map<string, FanInRunFeed>();

interface OrgRow {
  id: string;
  tenantId: string;
}

function log(fields: Record<string, unknown>): void {
  console.log(JSON.stringify({ ts: new Date().toISOString(), ...fields }));
}

async function listOrganizations(): Promise<OrgRow[]> {
  // The empty user context sets no org/user GUCs; visibility comes solely
  // from the SECURITY DEFINER list_organizations() function.
  return withUserContext("", async (client) => {
    const { rows } = await client.query<OrgRow>(
      'SELECT id, tenant_id AS "tenantId" FROM list_organizations()',
    );
    return rows;
  });
}

function feedFor(org: OrgRow): FanInRunFeed {
  let feed = feeds.get(org.id);
  if (!feed) {
    feed = new FanInRunFeed({ tenantId: org.tenantId });
    feeds.set(org.id, feed);
  }
  return feed;
}

async function tick(): Promise<void> {
  let orgs: OrgRow[];
  try {
    orgs = await listOrganizations();
  } catch (error) {
    log({ level: "error", op: "list_organizations", error: message(error) });
    return;
  }
  for (const org of orgs) {
    if (stopping) return;
    try {
      const feed = feedFor(org);
      const resolveRoutineId = (runId: string) => feed.agentIdForRun(runId);
      const consumed = await consumerTick(org.id, feed, { resolveRoutineId });
      const swept = await sweepTick(org.id, { resolveRoutineId });
      if (consumed.events > 0 || swept.notices > 0) {
        log({
          op: "tick",
          org_id: org.id,
          events: consumed.events,
          notifications: consumed.notifications,
          cursor: consumed.cursor,
          deadline_notices: swept.notices,
        });
      }
    } catch (error) {
      log({ level: "error", op: "tick", org_id: org.id, error: message(error) });
    }
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function shutdown(): Promise<void> {
  const holder = globalThis as { __convoyWebsitePool?: { end(): Promise<void> } };
  await holder.__convoyWebsitePool?.end().catch(() => undefined);
  log({ op: "stopped" });
}

async function main(): Promise<void> {
  log({ op: "started", interval_ms: intervalMs });
  process.on("SIGTERM", () => {
    stopping = true;
  });
  process.on("SIGINT", () => {
    stopping = true;
  });
  while (!stopping) {
    await tick();
    // Sleep in small slices so SIGTERM lands promptly between ticks.
    for (let waited = 0; waited < intervalMs && !stopping; waited += 200) {
      await sleep(Math.min(200, intervalMs - waited));
    }
  }
  await shutdown();
}

void main();
