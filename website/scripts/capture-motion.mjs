#!/usr/bin/env node
/**
 * Review evidence for the two sequences: before / mid / after frames and a
 * short recording of each at 1440x900 and 393x700, plus overflow smoke at
 * 320x568 and 844x390. Runs against a server:
 *   node scripts/capture-motion.mjs http://127.0.0.1:3100 <outDir>
 */
import { chromium } from "@playwright/test";
import { mkdirSync } from "node:fs";

const origin = process.argv[2] ?? "http://127.0.0.1:3100";
const out = process.argv[3] ?? "motion-evidence";
mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined });

async function sequence(width, height, which) {
  const context = await browser.newContext({ viewport: { width, height }, recordVideo: { dir: out, size: { width, height } }, hasTouch: width < 900 });
  const page = await context.newPage();
  await page.goto(origin + "/", { waitUntil: "networkidle" });
  const root = which === "hero" ? "[data-trace-hero]" : "[data-trace]";
  const button = which === "hero" ? "[data-hero-replay]" : "[data-trace-button]";
  const attr = which === "hero" ? "data-trace-hero" : "data-trace";
  const shot = (name) => page.screenshot({ path: `${out}/${which}-${width}-${name}.png` });
  // Bring the figure in view and let the autoplay finish, then a controlled replay for the frames.
  if (which === "hero") {
    await page.locator(root).scrollIntoViewIfNeeded();
  } else {
    if (width < 900) await page.locator("[data-trace-controls]").evaluate((el) => el.scrollIntoView({ block: "start", behavior: "instant" }));
    else await page.locator(button).scrollIntoViewIfNeeded();
  }
  await page.waitForFunction(([r, a]) => document.querySelector(r)?.getAttribute(a) !== "playing", [root, attr], { timeout: 6000 });
  await page.waitForTimeout(500);
  await shot("before");
  const box = await page.locator(button).boundingBox();
  if (width < 900) await page.touchscreen.tap(box.x + 20, box.y + 20);
  else await page.locator(button).click();
  await page.waitForTimeout(150);
  await shot("t0150");
  await page.waitForTimeout(900);
  await shot("t1050");
  await page.waitForTimeout(900);
  await shot("t1950");
  await page.waitForFunction(([r, a]) => document.querySelector(r)?.getAttribute(a) === "done", [root, attr], { timeout: 6000 });
  await page.waitForTimeout(700);
  await shot("after");
  const video = page.video();
  await context.close();
  const path = await video.path();
  console.log(`${which} ${width}x${height}: frames + ${path}`);
}

for (const [w, h] of [[1440, 900], [393, 700]]) {
  await sequence(w, h, "execution");
  await sequence(w, h, "hero");
}
for (const [w, h] of [[320, 568], [844, 390]]) {
  const page = await browser.newPage({ viewport: { width: w, height: h } });
  await page.goto(origin + "/", { waitUntil: "networkidle" });
  await page.locator("[data-trace-button]").scrollIntoViewIfNeeded();
  await page.locator("[data-trace-button]").click();
  await page.waitForTimeout(1000);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
  const state = await page.locator("[data-trace]").getAttribute("data-trace");
  await page.screenshot({ path: `${out}/smoke-${w}x${h}.png` });
  console.log(`smoke ${w}x${h}: state=${state} overflow=${overflow}`);
  await page.close();
}
await browser.close();
