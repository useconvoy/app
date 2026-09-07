#!/usr/bin/env node
/**
 * Measures the built page the way a reading-density review needs it: page
 * height, header height, each section's height and visible word count, and
 * where the primary hero action sits, at a set of viewports. Content-led
 * heights only; nothing here is a CSS constraint. Run against a server:
 *   node scripts/measure-page.mjs http://127.0.0.1:3100 [label]
 */
import { chromium } from "@playwright/test";

const origin = process.argv[2] ?? "http://127.0.0.1:3100";
const label = process.argv[3] ?? "";
const VIEWPORTS = [
  [1440, 900], [1280, 800], [393, 700], [402, 700], [440, 780], [393, 600], [320, 568], [844, 390],
];
const SECTIONS = ["top", "problem", "workflow", "execution", "partnership", "faq", "contact"];

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined });
const rows = [];
for (const [width, height] of VIEWPORTS) {
  const page = await browser.newPage({ viewport: { width, height } });
  await page.goto(origin + "/", { waitUntil: "networkidle" });
  const m = await page.evaluate((ids) => {
    const visibleWords = (el) => {
      const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      let words = 0;
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const p = n.parentElement;
        if (!p || !n.textContent.trim()) continue;
        const cs = getComputedStyle(p);
        if (cs.display === "none" || cs.visibility === "hidden") continue;
        if (p.closest("details:not([open]) > :not(summary)")) continue;
        if (p.closest(".sr-only")) continue;
        if (p.getBoundingClientRect().width === 0) continue;
        words += n.textContent.trim().split(/\s+/).length;
      }
      return words;
    };
    const cta = document.querySelector("#top a.btn-primary")?.getBoundingClientRect();
    return {
      page: document.documentElement.scrollHeight,
      header: document.querySelector("header").getBoundingClientRect().height,
      overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      cta: cta ? `${Math.round(cta.top)}–${Math.round(cta.bottom)}` : "n/a",
      sections: Object.fromEntries(
        ids.map((id) => {
          const el = document.getElementById(id);
          return [id, el ? { h: Math.round(el.getBoundingClientRect().height), w: visibleWords(el) } : null];
        }),
      ),
    };
  }, SECTIONS);
  rows.push({ viewport: `${width}x${height}`, ...m });
  await page.close();
}
await browser.close();

const short = { top: "hero", problem: "gap", workflow: "workflow", execution: "execution", partnership: "partner", faq: "faq", contact: "contact" };
console.log(`${label ? label + " " : ""}viewport | page | header | ${SECTIONS.map((s) => short[s] + " h/w").join(" | ")} | hero CTA y | overflow`);
for (const r of rows) {
  console.log(
    `${r.viewport} | ${r.page} | ${Math.round(r.header)} | ${SECTIONS.map((s) => `${r.sections[s].h}/${r.sections[s].w}`).join(" | ")} | ${r.cta} | ${r.overflow ? "YES" : "no"}`,
  );
}
