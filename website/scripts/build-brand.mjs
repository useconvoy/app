#!/usr/bin/env node
/**
 * Renders the brand assets under public/ from the two sources in
 * src/assets/brand/: the mark (SVG) and the share card (HTML using the
 * self-hosted Plex faces). Uses the Playwright Chromium already in
 * devDependencies; no other tooling. Run after changing either source or
 * BRAND_VERSION (icons) or SHARE_VERSION (card), then commit the output.
 * Also writes a review sheet with the 16/32/48 px favicons at pixel size and
 * the card at 600 and 360 px wide on a neutral surround.
 *
 *   node scripts/build-brand.mjs [review-dir]
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { chromium } from "@playwright/test";

import { contrast, inlineFonts, renderCard } from "./lib/share-card.mjs";

const ROOT = resolve(new URL("..", import.meta.url).pathname);
const PUBLIC = join(ROOT, "public");
const SOURCE = join(ROOT, "src/assets/brand");
const FONTS = join(ROOT, "src/assets/fonts");
const REVIEW = process.argv[2] ? resolve(process.argv[2]) : null;

const content = readFileSync(join(ROOT, "src/content/homepage.ts"), "utf8");
const VERSION = content.match(/export const BRAND_VERSION = "([^"]+)"/)?.[1];
const SHARE_VERSION = content.match(/export const SHARE_VERSION = "([^"]+)"/)?.[1];
if (!VERSION || !SHARE_VERSION) throw new Error("BRAND_VERSION or SHARE_VERSION not found in src/content/homepage.ts");

const tokens = readFileSync(join(ROOT, "src/styles/tokens.css"), "utf8");
const token = (name) => {
  const found = tokens.match(new RegExp(`--${name}:\\s*(\\S+?);`));
  if (!found) throw new Error(`token --${name} missing`);
  return found[1];
};

const markSvg = readFileSync(join(SOURCE, "convoy-mark.svg"), "utf8");
const cardHtml = inlineFonts(readFileSync(join(SOURCE, "share-card.html"), "utf8"), FONTS);
// The card source carries its own literal colors; they must be the tokens.
for (const [hex, name] of [["#F6F4EF", "background"], ["#A63D22", "accent"]]) {
  if (!cardHtml.includes(hex)) throw new Error(`share card: expected ${hex} (${name})`);
  if (token(name).toLowerCase() !== hex.toLowerCase()) throw new Error(`share card: ${hex} is not the current --${name} token (${token(name)})`);
}
for (const hex of ["#A63D22", "#F6F4EF"]) {
  if (!markSvg.includes(hex)) throw new Error(`mark: expected ${hex}`);
}

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_PATH || undefined;
const browser = await chromium.launch({ executablePath });
const context = await browser.newContext({ deviceScaleFactor: 1 });
const page = await context.newPage();

async function rasterMark(size) {
  await page.setViewportSize({ width: size, height: size });
  await page.setContent(
    `<!doctype html><html><head><style>html,body{margin:0;background:transparent}svg{display:block;width:${size}px;height:${size}px}</style></head><body>${markSvg}</body></html>`,
  );
  return page.screenshot({ type: "png", omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}

/** ICO container holding PNG images, which every current browser accepts. */
function ico(entries) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(entries.length, 4);
  const dir = [];
  const blobs = [];
  let offset = 6 + 16 * entries.length;
  for (const { size, png } of entries) {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size >= 256 ? 0 : size, 0);
    entry.writeUInt8(size >= 256 ? 0 : size, 1);
    entry.writeUInt8(0, 2); // palette
    entry.writeUInt8(0, 3); // reserved
    entry.writeUInt16LE(1, 4); // planes
    entry.writeUInt16LE(32, 6); // bits per pixel
    entry.writeUInt32LE(png.length, 8);
    entry.writeUInt32LE(offset, 12);
    offset += png.length;
    dir.push(entry);
    blobs.push(png);
  }
  return Buffer.concat([header, ...dir, ...blobs]);
}

mkdirSync(join(PUBLIC, "icons"), { recursive: true });
mkdirSync(join(PUBLIC, "share"), { recursive: true });

const written = [];
const write = (rel, data) => {
  writeFileSync(join(PUBLIC, rel), data);
  written.push(`${rel} (${data.length} bytes)`);
};

write(`icons/convoy-mark-${VERSION}.svg`, markSvg);
const pngs = {};
for (const size of [16, 32, 48, 96, 180, 192, 512]) pngs[size] = await rasterMark(size);
write("favicon.ico", ico([16, 32, 48].map((size) => ({ size, png: pngs[size] }))));
for (const size of [96, 180, 192, 512]) write(`icons/convoy-mark-${VERSION}-${size}.png`, pngs[size]);

// The card: every marked text element inside the 80 px column (x 80–800) and
// the art at or after x 760, all faces loaded. Essential text must pass 4.5:1.
const { png: card, report } = await renderCard(page, cardHtml, { margins: { left: 80, right: 800 }, artFrom: 760 });
const rgbToHex = (rgb) => `#${rgb.match(/\d+/g).slice(0, 3).map((n) => Number(n).toString(16).padStart(2, "0")).join("")}`;
for (const t of report.text) {
  const ratio = contrast(rgbToHex(t.color), rgbToHex(report.background));
  if (ratio < 4.5) throw new Error(`share card: ${t.name} contrast ${ratio.toFixed(2)}:1 is under 4.5:1`);
  written.push(`  ${t.name}: x ${t.left}–${t.right}, ${t.fontSize}, ${t.lines} line(s), contrast ${ratio.toFixed(2)}:1`);
}
const headline = report.text.find((t) => t.name === "headline");
if (!headline || headline.lines !== 2) throw new Error("share card: the headline must set on exactly two lines");
write(`share/convoy-card-${SHARE_VERSION}.png`, card);

if (REVIEW) {
  mkdirSync(REVIEW, { recursive: true });
  const data = (png) => `data:image/png;base64,${png.toString("base64")}`;
  const tab = (size, label) =>
    `<figure><div class="tab"><img src="${data(pngs[size])}" width="${size}" height="${size}" alt=""><span>Convoy | AI Model Deployment for Robots</span></div><figcaption>${label}</figcaption></figure>`;
  const zoom = (size) => `<figure><img class="px" src="${data(pngs[size])}" width="${size * 6}" height="${size * 6}" alt=""><figcaption>${size} px at 6×, pixel edges as rendered</figcaption></figure>`;
  await page.setViewportSize({ width: 1000, height: 1180 });
  await page.setContent(`<!doctype html><html><head><style>
    body{margin:0;padding:32px;background:#ffffff;color:#18221f;font:14px/1.4 system-ui,sans-serif;width:936px}
    h1{font-size:18px;margin:0 0 6px} h2{font-size:14px;margin:28px 0 10px;text-transform:uppercase;letter-spacing:.06em;color:#45534d}
    .row{display:flex;gap:28px;align-items:flex-start;flex-wrap:wrap} figure{margin:0} figcaption{font-size:12px;color:#45534d;margin-top:6px}
    .tab{display:inline-flex;align-items:center;gap:8px;padding:8px 12px;border:1px solid #d9d5cc;border-radius:8px 8px 0 0;background:#f1efe9;font-size:13px;color:#18221f;white-space:nowrap}
    .tab img{image-rendering:auto} .px{image-rendering:pixelated;border:1px solid #d9d5cc}
    .card{display:block;box-shadow:0 1px 3px rgba(0,0,0,.18)} .neutral{background:#e4e4e4;padding:24px;border-radius:4px}
  </style></head><body>
    <h1>Convoy brand assets: icons ${VERSION}, share card ${SHARE_VERSION}</h1>
    <div>Rendered by scripts/build-brand.mjs from src/assets/brand/. Mark geometry finalized in Claude Design; card selected by Codex review.</div>
    <h2>Favicon at actual pixel size</h2>
    <div class="row">${tab(16, "16 px (browser tab)")}${tab(32, "32 px (HiDPI tab, bookmarks)")}${tab(48, "48 px (search results, Windows)")}</div>
    <h2>Pixel structure</h2>
    <div class="row">${zoom(16)}${zoom(32)}</div>
    <h2>Larger renderings</h2>
    <div class="row">
      <figure><img src="${data(pngs[96])}" width="96" height="96" alt=""><figcaption>96 px PNG (search favicon, stable URL)</figcaption></figure>
      <figure><img src="${data(pngs[180])}" width="180" height="180" alt="" style="border-radius:40px"><figcaption>180 px apple-touch-icon (iOS masks the corners)</figcaption></figure>
      <figure><img src="${data(pngs[192])}" width="192" height="192" alt=""><figcaption>192 px manifest icon</figcaption></figure>
    </div>
    <h2>Share card ${SHARE_VERSION} at 600 px and 360 px wide</h2>
    <div class="row neutral"><figure><img class="card" src="${data(card)}" width="600" height="315" alt=""><figcaption>600 × 315</figcaption></figure>
    <figure><img class="card" src="${data(card)}" width="360" height="189" alt=""><figcaption>360 × 189</figcaption></figure></div>
  </body></html>`);
  const sheet = await page.screenshot({ type: "png", fullPage: true });
  writeFileSync(join(REVIEW, `brand-review-${VERSION}.png`), sheet);
  writeFileSync(join(REVIEW, `share-card-${SHARE_VERSION}.png`), card);
  written.push(`review sheet: ${join(REVIEW, `brand-review-${VERSION}.png`)}`);
}

await browser.close();
console.log(written.join("\n"));
