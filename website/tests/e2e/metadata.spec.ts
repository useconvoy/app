import { expect, test, type APIRequestContext } from "@playwright/test";

/**
 * Crawler-facing surface, checked on the raw HTML and the static endpoints
 * rather than through the DOM: what search engines, link unfurlers and
 * browsers asking for icons actually receive. Values are the approved
 * strings, so a copy drift fails here before it ships.
 */

const CANONICAL = "https://deployconvoy.com/";
const TITLE = "Convoy | AI Model Deployment for Robots";
const DESCRIPTION =
  "Convoy is building deployment infrastructure for physical AI, connecting trained models to robot sensors, compute, and controllers.";
const SOCIAL_TITLE = "Convoy — Deploy AI models to real robots";

function metaContent(html: string, attr: "name" | "property", key: string): string[] {
  const out: string[] = [];
  const re = /<meta\s+([^>]*?)\/?>/g;
  for (const m of html.matchAll(re)) {
    const attrs = Object.fromEntries(Array.from(m[1].matchAll(/([\w:-]+)="([^"]*)"/g), (a) => [a[1], a[2]]));
    if (attrs[attr] === key) out.push(attrs.content ?? "");
  }
  return out;
}

function links(html: string, rel: string): Record<string, string>[] {
  const out: Record<string, string>[] = [];
  for (const m of html.matchAll(/<link\s+([^>]*?)\/?>/g)) {
    const attrs = Object.fromEntries(Array.from(m[1].matchAll(/([\w:-]+)="([^"]*)"/g), (a) => [a[1], a[2]]));
    if (attrs.rel === rel) out.push(attrs);
  }
  return out;
}

function pngSize(bytes: Buffer): { width: number; height: number } {
  expect(bytes.subarray(0, 8).toString("hex"), "PNG signature").toBe("89504e470d0a1a0a");
  expect(bytes.subarray(12, 16).toString("ascii")).toBe("IHDR");
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

async function fetchOk(request: APIRequestContext, path: string, type: RegExp) {
  const res = await request.get(path);
  expect(res.status(), `${path} status`).toBe(200);
  expect(res.headers()["content-type"], `${path} content-type`).toMatch(type);
  return res.body();
}

test.describe("metadata and crawl surface", () => {
  test("raw HTML carries the approved title, description, canonical and robots directives", async ({ request }) => {
    const html = await (await request.get("/")).text();
    expect(html).toContain(`<title>${TITLE}</title>`);
    expect(metaContent(html, "name", "description")).toEqual([DESCRIPTION]);
    expect(links(html, "canonical").map((l) => l.href)).toEqual([CANONICAL]);
    expect(metaContent(html, "name", "robots")).toEqual(["index, follow, max-image-preview:large"]);
    expect(metaContent(html, "name", "googlebot")).toEqual(["index, follow, max-image-preview:large"]);
    expect(html).not.toMatch(/noindex|nofollow/);
    expect(html).toMatch(/<html lang="en"/);
    expect(metaContent(html, "name", "application-name")).toEqual(["Convoy"]);
    expect(links(html, "manifest").map((l) => l.href)).toEqual(["/manifest.webmanifest"]);
  });

  test("Open Graph and Twitter tags describe the page truthfully with an absolute, sized, typed image", async ({ request }) => {
    const html = await (await request.get("/")).text();
    const og = (key: string) => metaContent(html, "property", `og:${key}`);
    expect(og("title")).toEqual([SOCIAL_TITLE]);
    expect(og("description")).toEqual([DESCRIPTION]);
    expect(og("site_name")).toEqual(["Convoy"]);
    expect(og("type")).toEqual(["website"]);
    expect(og("locale")).toEqual(["en_US"]);
    expect(og("url")).toEqual([CANONICAL]);
    expect(og("image")).toHaveLength(1);
    expect(og("image")[0]).toMatch(/^https:\/\/deployconvoy\.com\/share\/convoy-card-[\w.-]+\.png$/);
    expect(og("image:width")).toEqual(["1200"]);
    expect(og("image:height")).toEqual(["630"]);
    expect(og("image:type")).toEqual(["image/png"]);
    expect(og("image:alt")[0]).toMatch(/Convoy mark and wordmark/);
    expect(og("image:alt")[0].length).toBeGreaterThan(40);
    const tw = (key: string) => metaContent(html, "name", `twitter:${key}`);
    expect(tw("card")).toEqual(["summary_large_image"]);
    expect(tw("title")).toEqual([SOCIAL_TITLE]);
    expect(tw("description")).toEqual([DESCRIPTION]);
    expect(tw("image")).toEqual(og("image"));
    expect(tw("image:alt")).toEqual(og("image:alt"));
    expect(tw("site")).toEqual([]);
    expect(tw("creator")).toEqual([]);
    expect(html).not.toMatch(/fb:app_id|twitter:site|twitter:creator/);
    // The share card is a real 1200 × 630 PNG at the URL in the tag, well under LinkedIn's 5 MB limit.
    const bytes = await fetchOk(request, new URL(og("image")[0]).pathname, /^image\/png/);
    expect(pngSize(bytes)).toEqual({ width: 1200, height: 630 });
    expect(bytes.length).toBeLessThan(5 * 1024 * 1024);
    // The card carries its own version, and the previous card stays served for pages that cached it.
    expect(og("image")[0]).toContain("/share/convoy-card-2026-09-2.png");
    expect(pngSize(await fetchOk(request, "/share/convoy-card-2026-09.png", /^image\/png/))).toEqual({ width: 1200, height: 630 });
    // The previous generated card is gone, so nothing links to two different images.
    expect((await request.get("/opengraph-image")).status()).toBe(404);
  });

  test("icons are declared and every declared icon is served with the right type and dimensions", async ({ request }) => {
    const html = await (await request.get("/")).text();
    const icons = links(html, "icon");
    const hrefs = icons.map((l) => l.href);
    expect(hrefs).toContain("/favicon.ico");
    expect(hrefs.some((h) => /^\/icons\/convoy-mark-[\w.-]+\.svg$/.test(h)), "svg icon").toBe(true);
    for (const size of [96, 192, 512]) {
      const entry = icons.find((l) => l.sizes === `${size}x${size}`);
      expect(entry, `${size}px icon link`).toBeTruthy();
      expect(entry!.type).toBe("image/png");
      const bytes = await fetchOk(request, entry!.href, /^image\/png/);
      expect(pngSize(bytes)).toEqual({ width: size, height: size });
    }
    const apple = links(html, "apple-touch-icon");
    expect(apple).toHaveLength(1);
    expect(apple[0].sizes).toBe("180x180");
    expect(pngSize(await fetchOk(request, apple[0].href, /^image\/png/))).toEqual({ width: 180, height: 180 });
    const svg = await fetchOk(request, hrefs.find((h) => h.endsWith(".svg"))!, /^image\/svg\+xml/);
    expect(svg.toString("utf8")).toContain('viewBox="0 0 64 64"');
    // favicon.ico at the root, an ICO container with 16, 32 and 48 px images.
    const ico = await fetchOk(request, "/favicon.ico", /image\/(x-icon|vnd\.microsoft\.icon)/);
    expect(ico.readUInt16LE(0)).toBe(0);
    expect(ico.readUInt16LE(2)).toBe(1);
    const count = ico.readUInt16LE(4);
    const sizes = Array.from({ length: count }, (_, i) => ico.readUInt8(6 + i * 16));
    expect(sizes).toEqual([16, 32, 48]);
    for (let i = 0; i < count; i++) {
      const length = ico.readUInt32LE(6 + i * 16 + 8);
      const offset = ico.readUInt32LE(6 + i * 16 + 12);
      expect(pngSize(ico.subarray(offset, offset + length))).toEqual({ width: sizes[i], height: sizes[i] });
    }
    expect((await request.get("/apple-touch-icon.png")).status(), "no stale apple-touch path is claimed").toBe(404);
  });

  test("manifest is a browser-mode bookmark manifest with the two large icons", async ({ request }) => {
    const res = await request.get("/manifest.webmanifest");
    expect(res.status()).toBe(200);
    expect(res.headers()["content-type"]).toMatch(/manifest\+json/);
    const manifest = await res.json();
    expect(manifest.name).toBe("Convoy");
    expect(manifest.display).toBe("browser");
    expect(manifest.start_url).toBe("/");
    expect(manifest.icons.map((i: { sizes: string }) => i.sizes)).toEqual(["192x192", "512x512"]);
    for (const icon of manifest.icons) {
      expect(icon.type).toBe("image/png");
      expect((await request.get(icon.src)).status()).toBe(200);
    }
    expect(manifest).not.toHaveProperty("serviceworker");
  });

  test("JSON-LD is valid, truthful and limited to WebSite and Organization", async ({ request }) => {
    const html = await (await request.get("/")).text();
    const scripts = Array.from(html.matchAll(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/g), (m) => m[1]);
    expect(scripts).toHaveLength(1);
    const data = JSON.parse(scripts[0]);
    expect(data["@context"]).toBe("https://schema.org");
    const types = data["@graph"].map((n: { "@type": string }) => n["@type"]).sort();
    expect(types).toEqual(["Organization", "WebSite"]);
    const org = data["@graph"].find((n: { "@type": string }) => n["@type"] === "Organization");
    const site = data["@graph"].find((n: { "@type": string }) => n["@type"] === "WebSite");
    expect(org["@id"]).toBe(`${CANONICAL}#organization`);
    expect(site["@id"]).toBe(`${CANONICAL}#website`);
    expect(org.name).toBe("Convoy");
    expect(site.name).toBe("Convoy");
    expect(org.url).toBe(CANONICAL);
    expect(site.url).toBe(CANONICAL);
    expect(org.description).toBe(DESCRIPTION);
    expect(site.description).toBe(DESCRIPTION);
    expect(org.email).toBe("founders@deployconvoy.com");
    expect(site.publisher).toEqual({ "@id": org["@id"] });
    // Logo: a crawlable square PNG of at least 112 px at an absolute URL.
    expect(org.logo["@type"]).toBe("ImageObject");
    expect(org.logo.url).toMatch(/^https:\/\/deployconvoy\.com\/icons\/.+\.png$/);
    expect(org.logo.width).toBeGreaterThanOrEqual(112);
    expect(org.logo.width).toBe(org.logo.height);
    const logo = await fetchOk(request, new URL(org.logo.url).pathname, /^image\/png/);
    expect(pngSize(logo)).toEqual({ width: org.logo.width, height: org.logo.height });
    // Nothing the page does not state: no address, legal name, profiles, ratings, products, FAQ.
    for (const node of data["@graph"]) {
      for (const key of ["address", "legalName", "sameAs", "aggregateRating", "review", "foundingDate", "telephone", "potentialAction"]) {
        expect(node, `${node["@type"]}.${key}`).not.toHaveProperty(key);
      }
    }
    expect(scripts[0]).not.toMatch(/<\//);
  });

  test("robots.txt and sitemap agree with the canonical URL", async ({ request }) => {
    const robots = await (await request.get("/robots.txt")).text();
    expect(robots).toMatch(/User-Agent: \*\s+Allow: \/\s/i);
    expect(robots).not.toMatch(/Disallow: \/\s*$/m);
    expect(robots).toContain("Disallow: /portal");
    expect(robots).toContain("Disallow: /api/portal");
    expect(robots).toContain("Sitemap: https://deployconvoy.com/sitemap.xml");
    const sitemapRes = await request.get("/sitemap.xml");
    expect(sitemapRes.status()).toBe(200);
    const sitemap = await sitemapRes.text();
    const locs = Array.from(sitemap.matchAll(/<loc>([^<]+)<\/loc>/g), (m) => m[1]);
    expect(locs).toEqual([CANONICAL]);
    const html = await (await request.get("/")).text();
    expect(links(html, "canonical")[0].href).toBe(locs[0]);
    expect(metaContent(html, "property", "og:url")[0]).toBe(locs[0]);
  });

  test("www is answered with a permanent redirect to the canonical apex URL", async ({ request }) => {
    for (const [path, target] of [
      ["/", CANONICAL],
      ["/?ref=x", `${CANONICAL}?ref=x`],
      ["/platform", `${CANONICAL}platform`],
    ] as const) {
      const res = await request.get(path, { headers: { host: "www.deployconvoy.com" }, maxRedirects: 0 });
      expect(res.status(), `www${path}`).toBe(308);
      expect(res.headers().location, `www${path} location`).toBe(target);
    }
    // The apex host and the compose-network health check are not redirected.
    expect((await request.get("/", { headers: { host: "deployconvoy.com" }, maxRedirects: 0 })).status()).toBe(200);
    expect((await request.get("/", { headers: { host: "web:3000" }, maxRedirects: 0 })).status()).toBe(200);
    // Legacy marketing paths still land on the page.
    const legacy = await request.get("/platform", { maxRedirects: 0 });
    expect(legacy.status()).toBe(308);
    expect(legacy.headers().location).toBe("/");
  });
});
