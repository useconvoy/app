#!/usr/bin/env node
/**
 * Rasterise the app icon set from the route mark.
 *
 * The wide mark is 30x14. At 16px that is about two pixels tall, so the small
 * sizes are not a mechanical downsample: 16px is drawn directly on the pixel
 * grid with 4px nodes and a 2px hole knocked out of the two open ones, which
 * is the smallest size at which "one done, two ahead" still reads. Everything
 * from 32px up renders the mark unchanged.
 *
 * Colors come from tokens.css so the icons cannot drift from the palette.
 *
 * Run with `node scripts/generate-icons.mjs`. Output is committed; this is
 * not part of the build.
 */
import { readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { PNG } from "pngjs";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const TOKENS = readFileSync(join(ROOT, "src/styles/tokens.css"), "utf8");

function token(name) {
  const found = TOKENS.match(new RegExp(`--${name}:\\s*(\\S+?);`));
  if (!found) throw new Error(`missing token --${name}`);
  return found[1];
}

const hex = (h) => [
  parseInt(h.slice(1, 3), 16),
  parseInt(h.slice(3, 5), 16),
  parseInt(h.slice(5, 7), 16),
];

const DEEP = hex(token("color-pine"));
const PAPER = hex(token("color-field"));
/** Dimmed rule so the nodes stay separate instead of fusing into a bar. */
const MID = DEEP.map((c, i) => Math.round(c + (PAPER[i] - c) * 0.45));

const SS = 8; // supersample factor for the large sizes

function canvas(size, fill) {
  const png = new PNG({ width: size, height: size });
  for (let i = 0; i < png.data.length; i += 4) {
    png.data[i] = fill[0];
    png.data[i + 1] = fill[1];
    png.data[i + 2] = fill[2];
    png.data[i + 3] = 255;
  }
  return png;
}

const px = (png, x, y, c, a = 255) => {
  if (x < 0 || y < 0 || x >= png.width || y >= png.height) return;
  const i = (png.width * y + x) << 2;
  png.data[i] = c[0];
  png.data[i + 1] = c[1];
  png.data[i + 2] = c[2];
  png.data[i + 3] = a;
};

function disc(png, cx, cy, r, c) {
  for (let y = Math.floor(cy - r); y <= Math.ceil(cy + r); y++)
    for (let x = Math.floor(cx - r); x <= Math.ceil(cx + r); x++)
      if ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) px(png, x, y, c);
}

function rect(png, x0, y0, x1, y1, c) {
  for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) px(png, x, y, c);
}

function roundCorners(png, radiusFrac = 0.222) {
  const s = png.width;
  const r = s * radiusFrac;
  for (let y = 0; y < s; y++)
    for (let x = 0; x < s; x++) {
      const cx = x < r ? r : x > s - r ? s - r : x;
      const cy = y < r ? r : y > s - r ? s - r : y;
      if ((x - cx) ** 2 + (y - cy) ** 2 > r * r) {
        const i = (s * y + x) << 2;
        png.data[i + 3] = 0;
      }
    }
}

/** The mark at large sizes, drawn supersampled then box-filtered down. */
function large(size, { bleed = false } = {}) {
  const s = size * SS;
  const png = canvas(s, DEEP);
  // Maskable icons need art inside the 80% safe circle; standard icons can
  // use more of the tile.
  const markW = s * (bleed ? 0.46 : 0.62);
  const scale = markW / 30;
  const x0 = (s - markW) / 2;
  const y = s / 2;
  const r = 3.4 * scale;
  const lw = Math.max(1, Math.round(scale));

  rect(png, Math.round(x0 + scale), Math.round(y - lw), Math.round(x0 + 29 * scale), Math.round(y + lw), PAPER);
  disc(png, x0 + 5 * scale, y, r, PAPER);
  for (const cx of [15, 25]) {
    disc(png, x0 + cx * scale, y, r, PAPER);
    disc(png, x0 + cx * scale, y, r - 2 * scale, DEEP);
  }

  // Box-filter down to the target size.
  const out = new PNG({ width: size, height: size });
  for (let y2 = 0; y2 < size; y2++)
    for (let x2 = 0; x2 < size; x2++) {
      let acc = [0, 0, 0, 0];
      for (let dy = 0; dy < SS; dy++)
        for (let dx = 0; dx < SS; dx++) {
          const i = (s * (y2 * SS + dy) + (x2 * SS + dx)) << 2;
          acc = [
            acc[0] + png.data[i],
            acc[1] + png.data[i + 1],
            acc[2] + png.data[i + 2],
            acc[3] + png.data[i + 3],
          ];
        }
      const n = SS * SS;
      const o = (size * y2 + x2) << 2;
      out.data[o] = Math.round(acc[0] / n);
      out.data[o + 1] = Math.round(acc[1] / n);
      out.data[o + 2] = Math.round(acc[2] / n);
      out.data[o + 3] = Math.round(acc[3] / n);
    }
  return out;
}

/** 16px, hand-fitted. A mechanical downsample loses the open checkpoints. */
function tiny16() {
  const png = canvas(16, DEEP);
  rect(png, 2, 8, 13, 8, MID);
  disc(png, 2.5, 7.5, 2, PAPER); // done: solid
  for (const cx of [8, 13]) {
    disc(png, cx - 0.5, 7.5, 2, PAPER); // ahead: ring
    rect(png, cx - 1, 7, cx, 8, DEEP); // hole
  }
  // A radius at this size chews visible bites out of the tile. Clipping the
  // four corner pixels is enough to stop it reading as a hard square.
  for (const [x, y] of [
    [0, 0],
    [15, 0],
    [0, 15],
    [15, 15],
  ]) {
    png.data[((16 * y + x) << 2) + 3] = 0;
  }
  return png;
}

/** app/ only serves the names Next has file conventions for. */
const saveApp = (png, name) =>
  writeFileSync(join(ROOT, "src/app", name), PNG.sync.write(png));
/** Everything the manifest links by URL has to be a real public asset. */
const savePublic = (png, name) =>
  writeFileSync(join(ROOT, "public", name), PNG.sync.write(png));

const rounded = (size) => {
  const p = large(size);
  roundCorners(p);
  return p;
};

// Apple wants an opaque square: transparency renders badly on iOS.
saveApp(large(180), "apple-icon.png");
savePublic(rounded(192), "icon-192.png");
savePublic(rounded(512), "icon-512.png");
// Maskable art must survive an aggressive crop, so it sits inside the safe
// circle and the background bleeds to every edge.
savePublic(large(512, { bleed: true }), "icon-maskable-512.png");

// favicon.ico: 16 hand-fitted, 32 and 48 from the master.
const icoSizes = [tiny16(), rounded(32), rounded(48)];
const buffers = icoSizes.map((p) => PNG.sync.write(p));
const header = Buffer.alloc(6);
header.writeUInt16LE(0, 0);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(buffers.length, 4);
let offset = 6 + 16 * buffers.length;
const entries = [];
buffers.forEach((buf, i) => {
  const e = Buffer.alloc(16);
  const size = icoSizes[i].width;
  e.writeUInt8(size === 256 ? 0 : size, 0);
  e.writeUInt8(size === 256 ? 0 : size, 1);
  e.writeUInt8(0, 2);
  e.writeUInt8(0, 3);
  e.writeUInt16LE(1, 4);
  e.writeUInt16LE(32, 6);
  e.writeUInt32LE(buf.length, 8);
  e.writeUInt32LE(offset, 12);
  offset += buf.length;
  entries.push(e);
});
writeFileSync(
  join(ROOT, "src/app/favicon.ico"),
  Buffer.concat([header, ...entries, ...buffers]),
);

console.log("wrote favicon.ico (16/32/48), apple-icon, 192, 512, maskable-512");
