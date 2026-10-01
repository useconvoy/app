import { expect, test as base, type APIRequestContext, type Page, type TestInfo } from "@playwright/test";

/*
 * Live journeys through Configurations on a deployed website (`pnpm run test:live`,
 * playwright.live.config.ts). They assume nothing about the workspace: names,
 * robots and evals are discovered on the page, so they work with any account's
 * document or with the sample. Steps that need something the workspace does not
 * have (a live device, a robot linked to a platform project, an episode with a
 * recording) are noted as annotations and skipped, never failed.
 *
 * Environment (never files, never printed):
 *   CONVOY_LIVE_BASE_URL, CONVOY_LIVE_EMAIL, CONVOY_LIVE_PASSWORD — required, else every test is skipped;
 *   CONVOY_LIVE_ALLOW_WRITES=1 — also run the write journey (W1). It reads the workspace
 *     document first and puts it back (or removes the one it created) in `finally`;
 *   CONVOY_LIVE_OUTPUT_DIR — where videos and milestone screenshots go.
 */
const BASE_URL = process.env.CONVOY_LIVE_BASE_URL ?? "";
const EMAIL = process.env.CONVOY_LIVE_EMAIL ?? "";
const PASSWORD = process.env.CONVOY_LIVE_PASSWORD ?? "";
const WRITES = process.env.CONVOY_LIVE_ALLOW_WRITES === "1";
const DOCUMENT = "/api/platform/workspace-documents/configurations";
/** A live device counts as fresh when its latest report is at most this old (heartbeats are about 15 s apart). */
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
const cards = (page: Page) => page.locator(".cv-config");
const rows = (page: Page, name: string) => page.getByRole("table", { name }).locator("tbody tr");
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

/** A screenshot of a milestone (full page, or the viewport under a dialog or the replay), with the account area masked. */
async function milestone(page: Page, testInfo: TestInfo, name: string, options: { overlay?: boolean } = {}) {
  const path = testInfo.outputPath(`${name}.png`);
  if (!options.overlay) await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path, fullPage: !options.overlay, animations: "disabled", mask: [page.locator(".cv-account")] });
  await testInfo.attach(name, { path, contentType: "image/png" });
}

/** Opens the index and returns how many configurations it lists (0 for an empty workspace). */
async function openIndex(page: Page): Promise<number> {
  await page.goto("/app/configurations");
  await expect(h1(page)).toHaveText("Configurations");
  await expect(cards(page).first().or(page.getByText("No configurations yet."))).toBeVisible();
  return cards(page).count();
}

/** The dashboards in index order, as { name, path }. */
async function configurations(page: Page): Promise<Array<{ name: string; path: string }>> {
  await openIndex(page);
  return cards(page).evaluateAll(nodes => nodes.map(node => ({ name: node.querySelector("h2")?.textContent?.trim() ?? "", path: node.getAttribute("href") ?? "" })));
}

/** The robots of a dashboard, as { name, path, type }. */
async function robotsOf(page: Page, path: string): Promise<Array<{ name: string; path: string; type: string }>> {
  await page.goto(path);
  await expect(page.getByRole("tab", { name: /^Robots/ })).toBeVisible();
  await expect(page.getByRole("table", { name: "Robots" })).toBeVisible();
  return rows(page, "Robots").evaluateAll(nodes => nodes.flatMap(node => {
    const link = node.querySelector("a.cv-row-link");
    return link ? [{ name: link.textContent?.trim() ?? "", path: link.getAttribute("href") ?? "", type: node.querySelectorAll("td")[0]?.textContent?.trim() ?? "" }] : [];
  }));
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

test("L1 · live, read-only: configurations, a robot's evals, an eval's rollouts and a real replay", async ({ page }, testInfo) => {
  await maskAccount(page);
  const list = await configurations(page);
  await milestone(page, testInfo, "L1-01-configurations");
  if (!list.length) return note(testInfo, "The workspace has no configurations.");

  // Prefer a robot with evals, and among those one whose eval has a replayable episode.
  let opened = false, replayed = false;
  for (const config of list) {
    for (const robot of await robotsOf(page, config.path)) {
      if (!opened) await milestone(page, testInfo, "L1-02-configuration");
      await page.goto(robot.path);
      await expect(h1(page)).toHaveText(robot.name);
      await page.getByRole("tab", { name: /^Evals/ }).click();
      const evals = rows(page, "Evals");
      await expect(evals.first()).toBeVisible({ timeout: 30_000 });
      const links = await evals.locator("a.cv-row-link").evaluateAll(nodes => nodes.slice(0, 6).map(node => ({ label: node.textContent?.trim() ?? "", path: node.getAttribute("href") ?? "" })));
      if (!links.length) continue;
      if (!opened) await milestone(page, testInfo, "L1-03-robot-evals");
      for (const run of links) {
        await page.goto(run.path);
        await expect(h1(page)).toHaveText(run.label);
        await expect(page.getByRole("group", { name: "Success rate", exact: true })).toBeVisible();
        if (!opened) { await milestone(page, testInfo, "L1-04-eval"); opened = true; }
        const replay = page.getByRole("button", { name: /^Replay / });
        if (!(await replay.count())) continue;
        await replay.first().click();
        await expect(page).toHaveURL(/[?&]rollout=/);
        const sheet = page.getByRole("dialog");
        const camera = sheet.getByRole("img", { name: /^Recorded robot camera at action \d+ of \d+$/ });
        const missing = sheet.getByText("No recording for this episode.");
        await expect(camera.or(missing).first()).toBeVisible({ timeout: 45_000 });
        if (await missing.count()) { note(testInfo, "An episode had no published recording."); await page.keyboard.press("Escape"); continue; }
        await sheet.getByRole("button", { name: "Play", exact: true }).click();
        await expect(sheet.locator(".cv-player__step")).not.toHaveText(/^0 \//, { timeout: 30_000 });
        await sheet.getByRole("button", { name: "Pause" }).click();
        await milestone(page, testInfo, "L1-05-replay", { overlay: true });
        await page.keyboard.press("Escape");
        await expect(sheet).toHaveCount(0);
        replayed = true;
        break;
      }
      if (replayed) break;
    }
    if (replayed) break;
  }
  if (!opened) note(testInfo, "No robot has evals.");
  else if (!replayed) note(testInfo, "No eval has a replayable episode.");
});

test("L2 · live, read-only: a live device's telemetry is fresh while it is online, and its traces", async ({ page }, testInfo) => {
  await maskAccount(page);
  for (const config of await configurations(page)) {
    await page.goto(config.path);
    await expect(h1(page)).toHaveText(config.name);
    const row = page.getByRole("region", { name: / telemetry$/ });
    if (!(await row.count())) continue;
    const status = (await row.locator(".cv-badge").first().textContent())?.trim() ?? "";
    await milestone(page, testInfo, "L2-01-telemetry");
    if (status === "Online") {
      const at = await row.locator("time").getAttribute("datetime");
      const age = at ? (Date.now() - Date.parse(at)) / 1000 : Number.NaN;
      expect(age, `latest device report (${at})`).toBeLessThanOrEqual(FRESH_S);
    } else {
      note(testInfo, `The device is ${status || "not reporting"} right now; freshness was not asserted.`);
    }
    const robot = (await row.locator("h2").textContent())?.trim() ?? "";
    await rows(page, "Robots").getByRole("link", { name: robot, exact: true }).click();
    await expect(h1(page)).toHaveText(robot);
    await page.getByRole("tab", { name: /^Traces/ }).click();
    await expect(page.getByRole("table", { name: "Traces" }).or(page.getByText("Connecting to the device…"))).toBeVisible();
    await milestone(page, testInfo, "L2-02-traces");
    return;
  }
  note(testInfo, "No configuration has a robot on a live device.");
});

/* ---------- write journey (opt-in), restoring the document ---------- */

test.describe("writes", () => {
  test.skip(!WRITES, "Set CONVOY_LIVE_ALLOW_WRITES=1 to run the write journey (it restores the workspace document).");

  test("W1 · live write: create a configuration, add a robot, then delete the configuration", async ({ page }, testInfo) => {
    await maskAccount(page);
    const name = `Live check ${Date.now().toString(36)}`;
    await withRestore(page, async () => {
      await openIndex(page);
      await page.getByRole("link", { name: "New configuration" }).first().click();
      await page.getByLabel("Name").fill(name);
      await page.getByLabel("Robot").fill("Live check arm");
      await page.getByLabel("Edge model").fill("Qwen2.5-1.5B-Instruct Q4_K_M");
      await page.getByRole("button", { name: "Create" }).click();
      await expect(h1(page)).toHaveText(name);
      await milestone(page, testInfo, "W1-01-created");

      await page.getByRole("button", { name: "Add robot" }).click();
      const dialog = page.getByRole("dialog", { name: "Add robot" });
      await dialog.getByLabel("Name").fill("Live check robot");
      await dialog.getByText("Simulator", { exact: true }).click();
      const projects = dialog.getByLabel("Evals from").locator("option");
      if (await projects.count() > 1) await dialog.getByLabel("Evals from").selectOption({ index: 1 });
      else note(testInfo, "The account lists no platform project; the robot has no platform evals.");
      await milestone(page, testInfo, "W1-02-add-robot", { overlay: true });
      await dialog.getByRole("button", { name: "Add robot" }).click();
      await expect(dialog).toBeHidden();
      await expect(rows(page, "Robots").filter({ hasText: "Live check robot" })).toBeVisible();
      await milestone(page, testInfo, "W1-03-robot-added");

      await page.getByRole("tab", { name: "Details" }).click();
      await page.getByRole("button", { name: "Delete configuration" }).click();
      await page.getByRole("dialog", { name: `Delete ${name}?` }).getByRole("button", { name: "Delete" }).click();
      await expect(h1(page)).toHaveText("Configurations");
      await expect(cards(page).filter({ hasText: name })).toHaveCount(0);
    });
  });
});
