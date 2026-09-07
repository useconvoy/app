#!/usr/bin/env node
/**
 * Renders the brand assets under public/ from the two sources in
 * src/assets/brand/: the mark (SVG) and the share card (HTML using the
 * self-hosted Plex faces). Uses the Playwright Chromium already in
 * devDependencies; no other tooling. Run after changing either source or
 * BRAND_VERSION, then commit the output. Also writes a review sheet with the
 * 16/32/48 px favicons at pixel size and the card at 300 px wide.
 *
 *   node scripts/build-brand.mjs [review-dir]
 */
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

import { chromium } from "@playwright/test";

const ROOT = resolve(new URL("..", import.meta.url).pathname);
const PUBLIC = join(ROOT, "public");
const SOURCE = join(ROOT, "src/assets/brand");
const FONTS = join(ROOT, "src/assets/fonts");
const REVIEW = process.argv[2] ? resolve(process.argv[2]) : null;

const content = readFileSync(join(ROOT, "src/content/homepage.ts"), "utf8");
const VERSION = content.match(/export const BRAND_VERSION = "([^"]+)"/)?.[1];
if (!VERSION) throw new Error("BRAND_VERSION not found in src/content/homepage.ts");

const tokens = readFileSync(join(ROOT, "src/styles/tokens.css"), "utf8");
const token = (name) => {
  const found = tokens.match(new RegExp(`--${name}:\\s*(\\S+?);`));
  if (!found) throw new Error(`token --${name} missing`);
  return found[1];
};

const markSvg = readFileSync(join(SOURCE, "convoy-mark.svg"), "utf8");
const cardHtml = readFileSync(join(SOURCE, "share-card.html"), "utf8")
  // A setContent() document has no file origin, so the faces go in as data URIs.
  .replace(/__FONTS__\/([\w.-]+\.woff2)/g, (_, file) => `data:font/woff2;base64,${readFileSync(join(FONTS, file)).toString("base64")}`)
  ;
// Design's source carries its own literal colors; they must be the tokens.
for (const [hex, name] of [["#F6F4EF", "background"], ["#18221F", "text-primary"], ["#A63D22", "accent"], ["#45534D", "text-secondary"]]) {
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

await page.setViewportSize({ width: 1200, height: 630 });
await page.setContent(cardHtml, { waitUntil: "load" });
// Browsers load a face only when text uses it; force every declared face so a
// missing or broken file fails here instead of silently falling back.
const fonts = await page.evaluate(async () => {
  const faces = Array.from(document.fonts);
  await Promise.all(faces.map((f) => f.load().catch(() => null)));
  return faces.map((f) => `${f.family} ${f.weight}: ${f.status}`);
});
const notLoaded = fonts.filter((f) => !f.endsWith(": loaded"));
if (fonts.length < 3 || notLoaded.length) throw new Error(`share card fonts: ${fonts.join(", ")}`);
const overflow = await page.evaluate(() => {
  // Everything that carries meaning stays inside the central square (x 285–915) and the card.
  const inside = (el) => { const r = el.getBoundingClientRect(); return r.left >= 285 && r.right <= 915 && r.top >= 0 && r.bottom <= 630; };
  const h = document.querySelector(".h1");
  return h.scrollWidth > h.clientWidth || h.getBoundingClientRect().height > 250 || ![".brand", ".h1", ".desc", ".dom"].every((s) => inside(document.querySelector(s)));
});
if (overflow) throw new Error("share card: text overflows or leaves the central 285–915 square");
const card = await page.screenshot({ type: "png", clip: { x: 0, y: 0, width: 1200, height: 630 } });
write(`share/convoy-card-${VERSION}.png`, card);

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
    .card{border:1px solid #d9d5cc;display:block}
  </style></head><body>
    <h1>Convoy brand assets ${VERSION}: review sheet</h1>
    <div>Rendered by scripts/build-brand.mjs from src/assets/brand/. Geometry and card finalized in Claude Design.</div>
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
    <h2>Share card at 300 px wide, then at 600 px</h2>
    <div class="row"><figure><img class="card" src="${data(card)}" width="300" height="158" alt=""><figcaption>300 × 158 (small link preview)</figcaption></figure>
    <figure><img class="card" src="${data(card)}" width="600" height="315" alt=""><figcaption>600 × 315</figcaption></figure></div>
  </body></html>`);
  const sheet = await page.screenshot({ type: "png", fullPage: true });
  writeFileSync(join(REVIEW, `brand-review-${VERSION}.png`), sheet);
  writeFileSync(join(REVIEW, `share-card-${VERSION}.png`), card);
  written.push(`review sheet: ${join(REVIEW, `brand-review-${VERSION}.png`)}`);
}

await browser.close();
console.log(written.join("\n"));
