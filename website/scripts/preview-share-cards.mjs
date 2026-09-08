#!/usr/bin/env node
/**
 * Renders link-preview card candidates from src/assets/brand/ to a review
 * directory without touching public/: each card as a full 1200 × 630 PNG,
 * a layout report (positions, font sizes, line counts, contrast of the
 * flat text colors against the background), and one contact sheet showing
 * every candidate at 600 × 315 and 360 × 189 on a neutral surround.
 *
 *   node scripts/preview-share-cards.mjs <review-dir> <template.html> [...]
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { basename, join, resolve } from "node:path";

import { chromium } from "@playwright/test";

import { CARD, contrast, inlineFonts, renderCard } from "./lib/share-card.mjs";

const ROOT = resolve(new URL("..", import.meta.url).pathname);
const FONTS = join(ROOT, "src/assets/fonts");
const [outArg, ...templates] = process.argv.slice(2);
if (!outArg || !templates.length) {
  console.error("usage: preview-share-cards.mjs <review-dir> <template.html> [...]");
  process.exit(2);
}
const OUT = resolve(outArg);
mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined });
const page = await (await browser.newContext({ deviceScaleFactor: 1 })).newPage();

const rgbToHex = (rgb) => {
  const m = rgb.match(/\d+/g);
  return m ? `#${m.slice(0, 3).map((n) => Number(n).toString(16).padStart(2, "0")).join("")}` : null;
};

const results = [];
for (const template of templates) {
  const name = basename(template, ".html");
  const html = inlineFonts(readFileSync(resolve(template), "utf8"), FONTS);
  const { png, report, fonts } = await renderCard(page, html);
  const bg = rgbToHex(report.background);
  const lines = report.text.map((t) => {
    const fg = rgbToHex(t.color);
    const ratio = fg && bg ? contrast(fg, bg).toFixed(2) : "?";
    return `  ${t.name}: x ${t.left}–${t.right}, y ${t.top}–${t.bottom}, ${t.fontSize}, ${t.lines} line(s), ${fg} on ${bg} = ${ratio}:1`;
  });
  writeFileSync(join(OUT, `${name}.png`), png);
  results.push({ name, png, lines, fonts });
  console.log(`${name}.png (${png.length} bytes, ${CARD.width}×${CARD.height})\n${lines.join("\n")}\n  fonts: ${fonts.join(", ")}`);
}

// Contact sheet: each candidate at 600 and 360 wide on a neutral surround.
const data = (png) => `data:image/png;base64,${png.toString("base64")}`;
const blocks = results
  .map(
    (r) => `<section><h2>${r.name}</h2><div class="row">
      <figure><img src="${data(r.png)}" width="600" height="315" alt=""><figcaption>600 × 315</figcaption></figure>
      <figure><img src="${data(r.png)}" width="360" height="189" alt=""><figcaption>360 × 189</figcaption></figure>
    </div></section>`,
  )
  .join("");
await page.setViewportSize({ width: 1080, height: 200 + results.length * 420 });
await page.setContent(`<!doctype html><html><head><style>
  body{margin:0;padding:40px;background:#e4e4e4;color:#333;font:14px/1.4 system-ui,sans-serif}
  h1{font-size:16px;margin:0 0 4px} p{margin:0 0 24px;color:#666}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.06em;margin:24px 0 10px;color:#555}
  .row{display:flex;gap:40px;align-items:flex-start} figure{margin:0} figcaption{font-size:12px;color:#666;margin-top:6px}
  img{display:block;box-shadow:0 1px 3px rgba(0,0,0,.18)}
</style></head><body><h1>Convoy share-card candidates</h1><p>Rendered from src/assets/brand/ by scripts/preview-share-cards.mjs at DPR 1; browser downscale from the 1200 × 630 PNG.</p>${blocks}</body></html>`);
const sheet = await page.screenshot({ type: "png", fullPage: true });
writeFileSync(join(OUT, "contact-sheet.png"), sheet);
console.log(`contact-sheet.png (${sheet.length} bytes)`);
await browser.close();
