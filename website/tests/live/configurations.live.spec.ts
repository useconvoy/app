import { expect, test as base, type APIRequestContext, type Locator, type Page, type TestInfo } from "@playwright/test";

/*
 * Live journeys through Configurations on a deployed website (`pnpm run test:live`,
 * playwright.live.config.ts). They assume nothing about the workspace: names,
 * robots and runs are discovered on the page, so they work with any account's
 * document or with the sample. Steps that need something the workspace does not
 * have (a device-bound robot, a gate run, a production robot with actions) are
 * noted as annotations and skipped, never failed.
 *
 * Environment (never files, never printed):
 *   CONVOY_LIVE_BASE_URL, CONVOY_LIVE_EMAIL, CONVOY_LIVE_PASSWORD — required, else every test is skipped;
 *   CONVOY_LIVE_ALLOW_WRITES=1 — also run the write journeys (J3, J5, J6). Each reads the
 *     workspace document first and puts it back (or removes the one it created) in `finally`;
 *   CONVOY_LIVE_OUTPUT_DIR — where videos and milestone screenshots go.
 */
const BASE_URL = process.env.CONVOY_LIVE_BASE_URL ?? "";
const EMAIL = process.env.CONVOY_LIVE_EMAIL ?? "";
const PASSWORD = process.env.CONVOY_LIVE_PASSWORD ?? "";
const WRITES = process.env.CONVOY_LIVE_ALLOW_WRITES === "1";
const DOCUMENT = "/api/platform/workspace-documents/configurations";
/** A measured value counts as fresh when its sample is at most this old (heartbeats are about 15 s apart). */
const FRESH_S = 180;

type StorageState = Awaited<ReturnType<APIRequestContext["storageState"]>>;

/** The BFF accepts writes only from its own origin with the web client marker. */
function writeHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { "Content-Type": "application/json", "X-Convoy-Client": "web", Origin: new URL(BASE_URL).origin, ...extra };
}

/** One sign-in per run, through the site's own route; every test's browser context starts signed in. */
const test = base.extend<object, { session: StorageState }>({
  session: [async ({ playwright }, provide) => {
    const request = await playwright.request.newContext({ baseURL: BASE_URL });
    const response = await request.post("/api/platform/auth/login", { headers: writeHeaders(), data: { email: EMAIL, password: PASSWORD } });
    if (!response.ok()) throw new Error(`Sign-in was refused (HTTP ${response.status()}); check the live account.`);
    await provide(await request.storageState());
    await request.post("/api/platform/auth/logout", { headers: writeHeaders(), data: {} }).catch(() => undefined);
    await request.dispose();
  }, { scope: "worker" }],
  storageState: async ({ session }, provide) => { await provide(session); },
});

test.skip(!(BASE_URL && EMAIL && PASSWORD), "Set CONVOY_LIVE_BASE_URL, CONVOY_LIVE_EMAIL and CONVOY_LIVE_PASSWORD to run the live journeys.");

/* ---------- page helpers ---------- */

const h1 = (page: Page) => page.locator("h1:visible");
const cards = (page: Page) => page.locator(".cfg-card:not(.cfg-card--add)");
const robotsTable = (page: Page) => page.getByRole("table").filter({ has: page.getByRole("columnheader", { name: /Planner p50/ }) });
const note = (testInfo: TestInfo, description: string) => testInfo.annotations.push({ type: "skipped step", description });

/** Replaces the account email in rendered text before it is painted, so videos and screenshots never show it. */
async function maskAccount(page: Page) {
  await page.addInitScript(({ email }) => {
    if (!email) return;
    const pattern = new RegExp(email.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
    const scrub = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        if (node.nodeValue && pattern.test(node.nodeValue)) node.nodeValue = node.nodeValue.replace(pattern, "[account]");
        pattern.lastIndex = 0;
        return;
      }
      const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
      for (let text = walker.nextNode(); text; text = walker.nextNode()) scrub(text);
    };
    const observer = new MutationObserver(records => {
      for (const record of records) {
        if (record.type === "characterData") scrub(record.target);
        else record.addedNodes.forEach(scrub);
      }
    });
    const start = () => { scrub(document.documentElement); observer.observe(document.documentElement, { subtree: true, childList: true, characterData: true }); };
    if (document.documentElement) start(); else document.addEventListener("DOMContentLoaded", start, { once: true });
  }, { email: EMAIL });
}

/** A screenshot of a milestone (full page, or the viewport under a dialog), with the account area masked. */
async function milestone(page: Page, testInfo: TestInfo, name: string, options: { overlay?: boolean } = {}) {
  const path = testInfo.outputPath(`${name}.png`);
  if (!options.overlay) await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: !options.overlay, animations: "disabled", mask: [page.locator(".cfg-account")] });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

/** Age in seconds from "Measured · just now / 9s ago / 2 min ago / 1 h ago / 3 d ago", or null. */
function measuredAge(label: string | null): number | null {
  const match = /Measured · (?:(just now)|(\d+)s ago|(\d+) min ago|(\d+) h ago|(\d+) d ago)/.exec(label ?? "");
  if (!match) return null;
  if (match[1]) return 0;
  const [seconds, minutes, hours, days] = match.slice(2).map(value => (value ? Number(value) : 0));
  return seconds + minutes * 60 + hours * 3600 + days * 86400;
}

/** Opens the index and returns how many configurations it lists (0 for an empty workspace). */
async function openIndex(page: Page): Promise<number> {
  await page.goto("/app/configurations");
  await expect(h1(page)).toHaveText("Configurations");
  await expect(cards(page).first().or(page.getByText("No configurations in this workspace yet."))).toBeVisible();
  return cards(page).count();
}

/** Opens configuration `index` from the index and returns its name and path. */
async function openConfiguration(page: Page, index: number): Promise<{ name: string; path: string }> {
  const card = cards(page).nth(index);
  const name = (await card.locator("h2").textContent())?.trim() ?? "";
  const path = (await card.getAttribute("href")) ?? "";
  await card.click();
  await expect(h1(page)).toHaveText(name);
  await expect(page.getByRole("tab", { name: "Overview" })).toBeVisible();
  return { name, path };
}

/** The first configuration whose dashboard shows `probe`, trying them in index order; null when none does. */
async function findConfiguration(page: Page, probe: (page: Page) => Promise<boolean>): Promise<{ name: string; path: string } | null> {
  const count = await openIndex(page);
  for (let i = 0; i < count; i++) {
    if (i > 0) await openIndex(page);
    const found = await openConfiguration(page, i);
    if (await probe(page)) return found;
  }
  return null;
}

/* ---------- workspace document: read before a write journey, restore after ---------- */

interface StoredDocument { exists: boolean; schemaVersion: number; body: unknown; revision: number | null }

async function readDocument(request: APIRequestContext): Promise<StoredDocument> {
  const response = await request.get(DOCUMENT, { headers: { "X-Convoy-Client": "web" } });
  if (response.status() === 404) return { exists: false, schemaVersion: 1, body: null, revision: null };
  if (!response.ok()) throw new Error(`The workspace document could not be read (HTTP ${response.status()}); a write journey needs a way back.`);
  const data = await response.json() as { schema_version: number; body: unknown; revision?: number };
  return { exists: true, schemaVersion: data.schema_version, body: data.body, revision: typeof data.revision === "number" ? data.revision : null };
}

/** Puts the original document back (or removes the one the journey created) and checks the result. */
async function restoreDocument(page: Page, original: StoredDocument) {
  // Leave the app first so nothing it still has in flight lands after the restore.
  await page.goto("about:blank").catch(() => undefined);
  const request = page.request;
  const current = await readDocument(request);
  const key = `live-restore-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  const match: Record<string, string> = current.revision !== null ? { "If-Match": `"${current.revision}"` } : {};
  if (!original.exists) {
    if (current.exists) {
      const response = await request.delete(DOCUMENT, { headers: writeHeaders({ "Idempotency-Key": key, ...match }) });
      expect(response.status(), "removing the workspace document the journey created").toBe(204);
    }
  } else {
    const precondition: Record<string, string> = current.exists ? match : { "If-None-Match": "*" };
    const response = await request.put(DOCUMENT, { headers: writeHeaders({ "Idempotency-Key": key, ...precondition }), data: { schema_version: original.schemaVersion, body: original.body } });
    expect(response.ok(), `putting the original workspace document back (HTTP ${response.status()})`).toBe(true);
  }
  const after = await readDocument(request);
  expect(after.exists, "the workspace document is as it was").toBe(original.exists);
  if (original.exists) expect(after.body, "the workspace document is as it was").toEqual(original.body);
}

/** Runs a write journey between a read of the document and its restore, always restoring. */
async function withRestore(page: Page, journey: () => Promise<void>) {
  const original = await readDocument(page.request);
  try { await journey(); }
  finally { await restoreDocument(page, original); }
}

/* ---------- read-only journeys ---------- */

test("J1 · live, read-only: a configuration, its device-bound robot, the latest gate run, its slices and a replay", async ({ page }, testInfo) => {
  await maskAccount(page);

  let configurations = 0;
  await test.step("The configurations index", async () => {
    configurations = await openIndex(page);
    await expect(page.locator(".cfg-notice").first()).toContainText("Values marked Sample are illustrative");
    await milestone(page, testInfo, "J1-01-configurations");
  });
  if (!configurations) return note(testInfo, "The workspace has no configurations.");

  await test.step("The first configuration's overview", async () => {
    await openConfiguration(page, 0);
    await expect(page.getByRole("region", { name: "Production KPIs" })).toBeVisible();
    await milestone(page, testInfo, "J1-02-configuration-overview");
  });

  await test.step("A device-bound robot: Measured, with a fresh sample while it is online", async () => {
    const devices = (p: Page) => p.getByRole("region", { name: /^Connected device · / });
    const found = await findConfiguration(page, async p => (await devices(p).count()) > 0);
    if (!found) return note(testInfo, "No configuration has a device-bound robot.");
    const device = devices(page).first();
    const robot = ((await device.getByRole("heading", { level: 2, name: /^Connected device · / }).textContent()) ?? "").replace(/^Connected device · /, "").trim();
    await milestone(page, testInfo, "J1-03-connected-device");
    await device.getByRole("link", { name: `Open ${robot}`, exact: true }).click();
    await expect(h1(page)).toHaveText(robot);
    const hero = page.locator("section.rb-board");
    await expect(hero).toBeVisible();
    await expect(hero.getByRole("heading", { name: "Waiting for the first device report" })).toHaveCount(0, { timeout: 45_000 });
    if (await hero.getByText("Online", { exact: true }).count()) {
      const measured = page.locator(".cfg-title .cfg-prov--measured");
      await expect(measured).toBeVisible();
      await expect(measured).toHaveText(/Measured · /);
      const age = measuredAge(await measured.textContent());
      expect(age, `latest measured sample age (${await measured.textContent()})`).not.toBeNull();
      expect(age!, "the latest measured sample is fresh").toBeLessThanOrEqual(FRESH_S);
      await expect(page.locator(".cfg-page-head + .portal-updated")).toContainText("Refreshes every 15 seconds");
    } else {
      note(testInfo, `${robot}'s device is not online right now; freshness was not asserted.`);
    }
    await milestone(page, testInfo, "J1-04-device-bound-robot");
    await page.goto(found.path);
    await expect(h1(page)).toHaveText(found.name);
  });

  const runLink = (p: Page) => p.getByRole("region", { name: "Production KPIs" }).getByRole("link", { name: /^Open Run \d+/ });
  let gated = false;
  await test.step("The latest gate run", async () => {
    if (!(await runLink(page).count()) && !(await findConfiguration(page, async p => (await runLink(p).count()) > 0))) return note(testInfo, "No configuration has a gated evaluation run.");
    await runLink(page).first().click();
    await expect(h1(page)).toHaveText(/^Run \d+ · /);
    await expect(page.locator(".cfg-title").getByText(/^(Passed gate|Below gate)$/)).toBeVisible();
    gated = true;
    await milestone(page, testInfo, "J1-05-gate-run");
  });

  await test.step("Scenario slices: filter the rollouts from the weakest slice", async () => {
    if (!gated) return note(testInfo, "No gate run to read slices from.");
    const slices = page.getByRole("table", { name: /success per slice/ });
    if (!(await slices.count())) return note(testInfo, "This run stores no scenario slices.");
    await expect(slices).toBeVisible();
    const filter = slices.getByRole("link", { name: "Filter rollouts" });
    if (!(await filter.count())) return note(testInfo, "No stored rollouts to filter by slice.");
    await filter.first().click();
    await expect(page).toHaveURL(/[?&]slice=/);
    await expect(page.getByRole("table", { name: /rollouts stored for Run/ })).toBeVisible();
    await milestone(page, testInfo, "J1-06-slice-rollouts");
  });

  await test.step("A replay, recorded when the run has one", async () => {
    if (!gated) return note(testInfo, "No gate run to replay.");
    await page.goto(page.url().replace(/\?.*$/, ""));
    await expect(h1(page)).toHaveText(/^Run \d+ · /);
    const runTitle = (await h1(page).textContent()) ?? "";
    const recordedRows = page.getByRole("table", { name: /rollouts stored for Run/ }).locator("tbody tr").filter({ has: page.locator(".cfg-prov--recorded") });
    const candidates: Locator[] = [
      page.getByRole("region", { name: "Recorded episode" }).getByRole("link", { name: /^Replay / }),
      page.getByRole("region", { name: "Recorded evaluation" }).getByRole("link", { name: /^Replay / }),
      recordedRows.getByRole("link", { name: /^Replay / }),
      page.getByRole("table", { name: /rollouts stored for Run/ }).getByRole("link", { name: /^Replay / }),
    ];
    let link: Locator | null = null, recorded = false;
    for (const [i, candidate] of candidates.entries()) if (!link && await candidate.count()) { link = candidate.first(); recorded = i < 3; }
    if (!link) return note(testInfo, "This run has no replays.");
    await link.click();
    await expect(page).toHaveURL(/[?&]rollout=/);
    await expect(page.locator(".ev-replay").getByText("Rollout replay", { exact: true })).toBeVisible();
    if (recorded) {
      const camera = page.getByRole("img", { name: /^Recorded robot camera at action \d+ of \d+$/ });
      const missing = page.getByText("No camera recording is available for this episode.", { exact: false });
      await expect(camera.or(missing).first()).toBeVisible({ timeout: 45_000 });
      if (await missing.count()) note(testInfo, "The recorded episode has no published camera recording.");
    } else {
      note(testInfo, "No recorded replay in this run; a sample replay was opened.");
      await expect(page.locator(".cfg-transport__pos")).toBeVisible();
    }
    await milestone(page, testInfo, "J1-07-replay");
    await page.keyboard.press("Escape");
    await expect(h1(page)).toHaveText(runTitle);
    await expect(page).not.toHaveURL(/[?&]rollout=/);
  });
});

test("J2 · live, read-only: a production robot's actions and a trace's span waterfall", async ({ page }, testInfo) => {
  await maskAccount(page);
  const production = (p: Page) => p.getByRole("group", { name: "Filter robots" }).getByRole("button", { name: /^Production\s*[1-9]/ });
  const found = await findConfiguration(page, async p => (await production(p).count()) > 0);
  if (!found) { note(testInfo, "No configuration has a production robot."); return; }

  const view = page.getByRole("button", { name: /^View trace · / });
  await test.step("A production robot from the dashboard, preferring one with stored actions", async () => {
    await production(page).first().click();
    const links = robotsTable(page).locator("tbody th[scope=row] .cfg-row-link");
    await expect(links.first()).toBeVisible();
    const robots = (await links.evaluateAll(nodes => nodes.map(node => ({ name: node.textContent?.trim() ?? "", href: node.getAttribute("href") ?? "" })))).slice(0, 8);
    for (const robot of robots) {
      await page.goto(robot.href);
      await expect(h1(page)).toHaveText(robot.name);
      await expect(page.locator(".cfg-page-head .portal-eyebrow")).toHaveText("Production robot");
      await expect(page.getByRole("tab", { name: /Actions & traces/ })).toHaveAttribute("aria-selected", "true");
      if (await view.count()) break;
    }
    await milestone(page, testInfo, "J2-01-production-robot");
  });

  await test.step("Actions & traces, and a trace drawer with its waterfall", async () => {
    if (!(await view.count())) return note(testInfo, "No production robot here has stored actions.");
    const button = view.first();
    await button.click();
    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(page).toHaveURL(/[?&]trace=/);
    await expect(drawer.locator(".cfg-wf").first()).toBeVisible();
    await milestone(page, testInfo, "J2-02-trace-drawer", { overlay: true });
    await page.keyboard.press("Escape");
    await expect(drawer).toHaveCount(0);
    await expect(button).toBeFocused();
  });
});

/* ---------- write journeys (opt-in), each restoring the document ---------- */

test.describe("writes", () => {
  test.skip(!WRITES, "Set CONVOY_LIVE_ALLOW_WRITES=1 to run the write journeys (they restore the workspace document).");

  test("J3 · live write: add a test robot after checking the production gate", async ({ page }, testInfo) => {
    await maskAccount(page);
    await withRestore(page, async () => {
      if (!(await openIndex(page))) { note(testInfo, "The workspace has no configurations."); return; }
      await openConfiguration(page, 0);
      await page.locator(".cfg-actions").getByRole("button", { name: "Add robot", exact: true }).click();
      const dialog = page.getByRole("dialog", { name: /^Add robot to / });
      await expect(dialog).toBeVisible();
      const choices = dialog.getByRole("group", { name: "Robot" }).locator("label.cfg-choice");
      const first = choices.first();
      const robot = ((await first.locator(".cfg-choice__title").textContent()) ?? "").trim();
      if (robot === "Pair a new device") { note(testInfo, "No registered robot is free to add."); return; }
      await first.getByRole("radio").check();
      const production = dialog.getByRole("radio", { name: /^Production/ });
      if (await production.isDisabled()) await expect(dialog).toContainText("Not available:");
      else { await production.check(); await expect(dialog.getByRole("status").filter({ hasText: /passed gate on Run \d+/ })).toBeVisible(); }
      await milestone(page, testInfo, "J3-01-add-robot", { overlay: true });
      await dialog.getByRole("radio", { name: /^Test/ }).check();
      await dialog.getByRole("button", { name: "Add robot", exact: true }).click();
      await expect(dialog).toBeHidden();
      await expect(page.locator(".cd-flash")).toContainText(`${robot} added to`);
      await page.getByRole("tab", { name: /^Robots/ }).click();
      await expect(robotsTable(page).getByRole("row").filter({ hasText: robot })).toContainText("Test");
      await milestone(page, testInfo, "J3-02-robot-added");
    });
  });

  test("J5 · live write: flag a robot with a note, see it in the attention banner, clear it", async ({ page }, testInfo) => {
    await maskAccount(page);
    const reason = `Live journey check ${Date.now().toString(36)}`;
    await withRestore(page, async () => {
      if (!(await openIndex(page))) { note(testInfo, "The workspace has no configurations."); return; }
      await openConfiguration(page, 0);
      await page.getByRole("tab", { name: /^Robots/ }).click();
      const flagButton = page.getByRole("button", { name: /^Flag / });
      if (!(await flagButton.count())) { note(testInfo, "No unflagged robot in this configuration."); return; }
      const robot = ((await flagButton.first().textContent()) ?? "").replace(/^Flag\s*/, "").trim();
      await flagButton.first().click();
      const dialog = page.getByRole("dialog", { name: `Flags · ${robot}` });
      await dialog.getByLabel("Reason").fill(reason);
      await dialog.getByLabel("Note").fill("Added and cleared by the live journey test.");
      await dialog.getByRole("radio", { name: /^Needs attention/ }).check();
      await dialog.getByRole("button", { name: "Flag robot" }).click();
      await expect(dialog).toBeHidden();
      const banner = page.locator(".cd-banner");
      await expect(banner.getByRole("link", { name: robot, exact: true })).toBeVisible();
      await expect(robotsTable(page).getByRole("row").filter({ hasText: robot })).toContainText("Needs attention");
      await milestone(page, testInfo, "J5-01-flagged");
      await page.getByRole("button", { name: new RegExp(`^Review \\d+ flags? on ${robot.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`) }).click();
      await dialog.getByRole("button", { name: `Clear flag “${reason}”` }).click();
      await expect(dialog.getByRole("status").filter({ hasText: "Cleared" })).toContainText(reason);
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      await milestone(page, testInfo, "J5-02-cleared");
    });
  });

  test("J6 · live write: queue an evaluation on a test robot; it says no runner is connected", async ({ page }, testInfo) => {
    await maskAccount(page);
    await withRestore(page, async () => {
      const test = (p: Page) => p.getByRole("group", { name: "Filter robots" }).getByRole("button", { name: /^Test\s*[1-9]/ });
      const found = await findConfiguration(page, async p => (await test(p).count()) > 0);
      if (!found) { note(testInfo, "No configuration has a test robot."); return; }
      await test(page).first().click();
      const first = robotsTable(page).locator("tbody th[scope=row] .cfg-row-link").first();
      const robot = ((await first.textContent()) ?? "").trim();
      await first.click();
      await expect(h1(page)).toHaveText(robot);
      const run = page.getByRole("button", { name: "Run evaluation" });
      if (await run.getAttribute("aria-disabled") === "true") { note(testInfo, "Run evaluation is unavailable here."); return; }
      await run.click();
      const dialog = page.getByRole("dialog", { name: `Run evaluation on ${robot}` });
      await expect(dialog).toContainText("No evaluation runner is connected");
      const queue = dialog.getByRole("button", { name: "Queue run" });
      if (await queue.isDisabled()) { note(testInfo, "No evaluation suite to queue."); return; }
      await queue.click();
      await expect(dialog).toBeHidden();
      await expect(page.getByRole("note").filter({ hasText: `is queued on ${robot}` })).toContainText("No evaluation runner is connected, so it has not started.");
      await milestone(page, testInfo, "J6-01-queued");
    });
  });
});
