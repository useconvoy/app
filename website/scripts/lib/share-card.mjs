/**
 * Shared rendering for the link-preview card: inlines the self-hosted Plex
 * faces (a setContent() document has no file origin), renders a 1200 × 630
 * PNG at DPR 1, and checks that every face loaded and that the marked text
 * sits inside its margins. Used by build-brand.mjs for the shipped card and
 * by preview-share-cards.mjs for candidates.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";

export const CARD = { width: 1200, height: 630 };

export function inlineFonts(html, fontsDir) {
  return html.replace(/__FONTS__\/([\w.-]+\.woff2)/g, (_, file) => `data:font/woff2;base64,${readFileSync(join(fontsDir, file)).toString("base64")}`);
}

/** Relative luminance and WCAG contrast for flat hex colors. */
export function contrast(hexA, hexB) {
  const lum = (hex) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const [hi, lo] = [lum(hexA), lum(hexB)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * Renders one card. `margins` is { left, right } in px: every element with
 * data-text must start at or after `left` and end at or before `right`,
 * and every element with data-art must start at or after `artFrom`.
 */
export async function renderCard(page, html, { margins = { left: 72, right: 760 }, artFrom = 760 } = {}) {
  await page.setViewportSize(CARD);
  await page.setContent(html, { waitUntil: "load" });
  const fonts = await page.evaluate(async () => {
    const faces = Array.from(document.fonts);
    await Promise.all(faces.map((f) => f.load().catch(() => null)));
    return faces.map((f) => `${f.family} ${f.weight}: ${f.status}`);
  });
  const notLoaded = fonts.filter((f) => !f.endsWith(": loaded"));
  if (fonts.length < 2 || notLoaded.length) throw new Error(`card fonts: ${fonts.join(", ")}`);
  const report = await page.evaluate(
    ({ margins, artFrom, width, height }) => {
      const problems = [];
      const text = Array.from(document.querySelectorAll("[data-text]")).map((el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        const lines = Math.round(r.height / parseFloat(cs.lineHeight));
        if (r.left < margins.left - 0.5 || r.right > margins.right + 0.5 || r.top < 0 || r.bottom > height) problems.push(`${el.dataset.text}: ${Math.round(r.left)}–${Math.round(r.right)} × ${Math.round(r.top)}–${Math.round(r.bottom)} leaves its margins`);
        if (el.scrollWidth > el.clientWidth + 1) problems.push(`${el.dataset.text}: text overflows its box`);
        return { name: el.dataset.text, left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), fontSize: cs.fontSize, lines, color: cs.color };
      });
      for (const el of document.querySelectorAll("[data-art]")) {
        const r = el.getBoundingClientRect();
        if (r.left < artFrom - 0.5) problems.push(`${el.dataset.art}: art starts at ${Math.round(r.left)}, before ${artFrom}`);
      }
      if (document.documentElement.scrollWidth > width || document.documentElement.scrollHeight > height) problems.push("card overflows the canvas");
      return { text, problems, background: getComputedStyle(document.body).backgroundColor };
    },
    { margins, artFrom, ...CARD },
  );
  if (report.problems.length) throw new Error(`card layout: ${report.problems.join("; ")}`);
  const png = await page.screenshot({ type: "png", clip: { x: 0, y: 0, ...CARD } });
  return { png, report, fonts };
}
