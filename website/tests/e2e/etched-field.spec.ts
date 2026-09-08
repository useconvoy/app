import { expect, test, type Locator, type Page } from "@playwright/test";

/**
 * The etched field behind the hero figure: decorative rings that must obey
 * the visitor, the OS, visibility and the page's own layout. Real browser
 * input (Playwright mouse, and CDP touch events for the phone cases) drives
 * every wave; the field's data-etched-field attribute reports why it is or
 * is not running.
 */

const FIELD = "[data-etched-field]";
const RINGS = `${FIELD} .etched-ring`;

async function fieldBox(page: Page) {
  const box = await page.locator(FIELD).boundingBox();
  if (!box) throw new Error("etched field is not laid out");
  return box;
}

async function ringOrigins(rings: Locator) {
  return rings.evaluateAll((els) => els.map((el) => ({ wave: el.getAttribute("data-wave"), x: parseFloat((el as HTMLElement).style.left), y: parseFloat((el as HTMLElement).style.top) })));
}

async function waves(page: Page) {
  const origins = await ringOrigins(page.locator(RINGS));
  return [...new Set(origins.map((o) => o.wave))];
}

test.describe("etched field", () => {
  test("server markup is deterministic: grid and empty wave layer, decorative and unfocusable, no controls in the hero", async ({ request }) => {
    const html = await (await request.get("/")).text();
    expect(html).toContain('data-etched-field="inactive"');
    expect(html).toMatch(/<div aria-hidden="true" class="etched-grid"><\/div><div aria-hidden="true" class="etched-waves"><\/div>/);
    expect(html).not.toContain("etched-ring");
    const hero = html.slice(html.indexOf('id="top"'), html.indexOf('id="problem"') > 0 ? html.indexOf('id="problem"') : html.indexOf("</section>"));
    expect(hero).not.toMatch(/<button/);
    expect(hero).not.toMatch(/Replay|Trace the path|Playing/);
    // The one control lives in the footer: an action button offering "Pause motion", with no pressed state.
    expect(html).toMatch(/<footer[\s\S]*data-motion-toggle[\s\S]*<\/footer>/);
    expect(html).not.toMatch(/data-motion-toggle[^>]*aria-pressed|aria-pressed[^>]*data-motion-toggle/);
    expect(html).toContain("Pause motion");
  });

  test("idle waves start at random exposed places every few seconds, never more than six rings, and rings stay inside the field", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const field = page.locator(FIELD);
    await expect(field).toHaveAttribute("data-etched-field", "active");
    // The pointer is parked outside the field, so only idle origins appear.
    await page.mouse.move(5, 5);
    await expect(page.locator(RINGS)).not.toHaveCount(0, { timeout: 6500 });
    const size = await page.locator(`${FIELD} .etched-waves`).evaluate((el) => ({ w: el.clientWidth, h: el.clientHeight }));
    const origins = await ringOrigins(page.locator(RINGS));
    expect(origins.length).toBe(3);
    for (const o of origins) {
      expect(o.x).toBeGreaterThanOrEqual(0);
      expect(o.x).toBeLessThanOrEqual(size.w);
      expect(o.y).toBeGreaterThanOrEqual(0);
      expect(o.y).toBeLessThanOrEqual(size.h);
      const margin = o.x <= 0.24 * size.w || o.x >= 0.76 * size.w || o.y <= 0.1 * size.h || o.y >= 0.9 * size.h;
      expect(margin, `idle origin ${o.x},${o.y} favors the margins`).toBe(true);
    }
    // A second wave follows within the idle window (the first may already have faded); any remaining first-wave rings keep their center.
    const first = origins[0];
    await expect.poll(async () => (await waves(page)).some((id) => id !== first.wave), { timeout: 6500 }).toBe(true);
    const later = await ringOrigins(page.locator(RINGS));
    expect(later.filter((o) => o.wave === first.wave).every((o) => o.x === first.x && o.y === first.y)).toBe(true);
    expect(new Set(later.map((o) => o.wave)).size).toBeLessThanOrEqual(2);
    expect(later.length).toBeLessThanOrEqual(6);
    // Rings are decoration only.
    expect(await page.locator(`${FIELD} .etched-waves`).getAttribute("aria-hidden")).toBe("true");
    expect(await page.locator(`${FIELD} .etched-grid`).getAttribute("aria-hidden")).toBe("true");
    await expect(page.locator(`${FIELD} [tabindex], ${FIELD} .etched-waves *:focus`)).toHaveCount(0);
  });

  test("mouse entry starts a wave at the pointer, later waves follow meaningful movement, the cap retires the oldest wave, and old rings keep their centers", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    await page.mouse.move(5, 5);
    const box = await fieldBox(page);
    const at = async (dx: number, dy: number) => {
      // One pointermove per step, so the wave origin is the position asserted here.
      await page.mouse.move(box.x + dx, box.y + dy, { steps: 1 });
      return { x: dx, y: dy };
    };
    const p1 = await at(40, 60);
    await expect(page.locator(RINGS)).toHaveCount(3);
    let origins = await ringOrigins(page.locator(RINGS));
    for (const o of origins) {
      expect(Math.abs(o.x - p1.x)).toBeLessThanOrEqual(1);
      expect(Math.abs(o.y - p1.y)).toBeLessThanOrEqual(1);
    }
    // Small movement inside the threshold starts nothing.
    await page.waitForTimeout(700);
    await at(50, 65);
    await page.waitForTimeout(100);
    expect(await waves(page)).toHaveLength(1);
    // Meaningful movement after the interval starts a second wave at the new position; the first keeps its center.
    const p2 = await at(120, 100);
    await expect.poll(() => waves(page)).toHaveLength(2);
    origins = await ringOrigins(page.locator(RINGS));
    const ids = [...new Set(origins.map((o) => o.wave))];
    expect(origins.filter((o) => o.wave === ids[0]).every((o) => Math.abs(o.x - p1.x) <= 1)).toBe(true);
    expect(origins.filter((o) => o.wave === ids[1]).every((o) => Math.abs(o.x - p2.x) <= 1 && Math.abs(o.y - p2.y) <= 1)).toBe(true);
    // Movement before the interval elapses starts nothing, even at the cap.
    await at(200, 140);
    await page.waitForTimeout(100);
    expect(await waves(page)).toHaveLength(2);
    // At the cap, an intentional wave retires the oldest, so there are never more than six rings.
    await page.waitForTimeout(700);
    const p3 = await at(280, 180);
    await expect.poll(async () => (await ringOrigins(page.locator(RINGS))).some((o) => Math.abs(o.x - p3.x) <= 1 && Math.abs(o.y - p3.y) <= 1)).toBe(true);
    origins = await ringOrigins(page.locator(RINGS));
    expect(origins.length).toBeLessThanOrEqual(6);
    expect(new Set(origins.map((o) => o.wave)).size).toBeLessThanOrEqual(2);
    expect(origins.some((o) => o.wave === ids[0]), "the oldest wave was retired").toBe(false);
    // Leaving the field resumes idle origins: the next wave is not at the last pointer position.
    await page.mouse.move(5, 5);
    await page.waitForTimeout(3300);
    await expect.poll(async () => (await ringOrigins(page.locator(RINGS))).some((o) => Math.abs(o.x - p3.x) > 2 || Math.abs(o.y - p3.y) > 2), { timeout: 6500 }).toBe(true);
  });

  test("a resize clears rings and stale coordinates, and the next hover uses the new bounds", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    await page.mouse.move(5, 5);
    let box = await fieldBox(page);
    await page.mouse.move(box.x + 60, box.y + 80, { steps: 3 });
    await expect(page.locator(RINGS)).toHaveCount(3);
    // The mouse stays inside the field across the resize.
    await page.setViewportSize({ width: 1024, height: 768 });
    await expect(page.locator(RINGS)).toHaveCount(0);
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    box = await fieldBox(page);
    expect(box.width).toBeLessThan(700);
    // Its next meaningful move re-establishes the origin from the new bounds, without leaving and re-entering.
    await page.waitForTimeout(700);
    await page.mouse.move(box.x + 30, box.y + 45, { steps: 1 });
    await expect(page.locator(RINGS)).toHaveCount(3);
    let origins = await ringOrigins(page.locator(RINGS));
    for (const o of origins) {
      expect(Math.abs(o.x - 30)).toBeLessThanOrEqual(1);
      expect(Math.abs(o.y - 45)).toBeLessThanOrEqual(1);
    }
    // And a fresh entry after leaving also uses the new bounds.
    await page.mouse.move(5, 5);
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.locator(RINGS)).toHaveCount(0);
    box = await fieldBox(page);
    await page.mouse.move(box.x + 70, box.y + 35, { steps: 1 });
    await expect(page.locator(RINGS)).toHaveCount(3);
    origins = await ringOrigins(page.locator(RINGS));
    for (const o of origins) {
      expect(Math.abs(o.x - 70)).toBeLessThanOrEqual(1);
      expect(Math.abs(o.y - 35)).toBeLessThanOrEqual(1);
    }
  });

  test("pause still holds when session storage is blocked", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => {
      Object.defineProperty(window, "sessionStorage", {
        configurable: true,
        get() {
          throw new DOMException("blocked", "SecurityError");
        },
      });
    });
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    const toggle = page.locator("[data-motion-toggle]");
    await toggle.scrollIntoViewIfNeeded();
    await toggle.click();
    await expect(toggle).toHaveText("Resume motion");
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "paused");
    await page.evaluate(() => window.scrollTo(0, 0));
    const box = await fieldBox(page);
    await page.mouse.move(box.x + 50, box.y + 50, { steps: 1 });
    await page.waitForTimeout(5500);
    await expect(page.locator(RINGS)).toHaveCount(0);
    await toggle.scrollIntoViewIfNeeded();
    await toggle.click();
    await expect(toggle).toHaveText("Pause motion");
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    expect(errors).toEqual([]);
  });

  test("pause and resume from the footer, kept for the visit", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    await page.mouse.move(5, 5);
    await expect(page.locator(RINGS)).not.toHaveCount(0, { timeout: 6500 });
    const toggle = page.locator("[data-motion-toggle]");
    await expect(toggle).toHaveText("Pause motion");
    await expect(toggle).not.toHaveAttribute("aria-pressed", /.*/);
    await toggle.scrollIntoViewIfNeeded();
    await toggle.click();
    await expect(toggle).toHaveText("Resume motion");
    await expect(toggle).not.toHaveAttribute("aria-pressed", /.*/);
    await expect(page.locator(RINGS)).toHaveCount(0);
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "paused");
    // Nothing starts while paused, even with the pointer over the field.
    await page.evaluate(() => window.scrollTo(0, 0));
    const box = await fieldBox(page);
    await page.mouse.move(box.x + 50, box.y + 50, { steps: 3 });
    await page.waitForTimeout(5500);
    await expect(page.locator(RINGS)).toHaveCount(0);
    // The choice survives a reload within the visit.
    await page.reload();
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "paused");
    await expect(toggle).toHaveText("Resume motion");
    await toggle.scrollIntoViewIfNeeded();
    await toggle.click();
    await expect(toggle).toHaveText("Pause motion");
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    await expect(page.locator(RINGS)).not.toHaveCount(0, { timeout: 6500 });
    // The control is a comfortable target.
    const h = await toggle.evaluate((el) => el.getBoundingClientRect().height);
    expect(h).toBeGreaterThanOrEqual(44);
  });

  test("OS reduced motion wins at load and when it changes during the visit", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "reduced");
    const box = await fieldBox(page);
    await page.mouse.move(box.x + 50, box.y + 50, { steps: 3 });
    await page.waitForTimeout(5500);
    await expect(page.locator(RINGS)).toHaveCount(0);
    // The grid stays as a static texture; nothing animates.
    await expect(page.locator(`${FIELD} .etched-grid`)).toBeAttached();
    // Preference lifted mid-visit: the field starts.
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    await page.mouse.move(box.x + 80, box.y + 90, { steps: 3 });
    await expect(page.locator(RINGS)).toHaveCount(3);
    // Preference set mid-visit: rings are removed at once and nothing restarts.
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "reduced");
    await expect(page.locator(RINGS)).toHaveCount(0);
    await page.mouse.move(box.x + 120, box.y + 120, { steps: 3 });
    await page.waitForTimeout(800);
    await expect(page.locator(RINGS)).toHaveCount(0);
    // Reduced motion also outranks the footer control: resuming there changes nothing.
    const toggle = page.locator("[data-motion-toggle]");
    await toggle.scrollIntoViewIfNeeded();
    await toggle.click();
    await toggle.click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "reduced");
  });

  test("offscreen, hidden document and unmount all stop the field and remove rings", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    const box = await fieldBox(page);
    await page.mouse.move(box.x + 50, box.y + 50, { steps: 3 });
    await expect(page.locator(RINGS)).toHaveCount(3);
    // Scrolled well past the hero: no rings and no new ones.
    await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "offscreen");
    await expect(page.locator(RINGS)).toHaveCount(0);
    await page.waitForTimeout(5500);
    await expect(page.locator(RINGS)).toHaveCount(0);
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    // Hidden document (tab in the background).
    await page.mouse.move(box.x + 60, box.y + 70, { steps: 3 });
    await expect(page.locator(RINGS)).toHaveCount(3);
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "hidden");
    await expect(page.locator(RINGS)).toHaveCount(0);
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => false });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.locator(FIELD)).toHaveAttribute("data-etched-field", "active");
    // Navigating away unmounts the field: its timers are gone with the document, and no error surfaces.
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    await page.goto("/platform");
    await page.waitForTimeout(500);
    expect(errors).toEqual([]);
  });

  test("layout: no sideways scroll, crisp foreground above the field, 44px controls at 320, 393, 430 and 1440", async ({ page }) => {
    for (const width of [320, 393, 430, 1440]) {
      await page.setViewportSize({ width, height: width < 600 ? 852 : 900 });
      await page.goto("/");
      const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
      expect(scrollWidth, `no sideways scroll at ${width}`).toBeLessThanOrEqual(clientWidth);
      const field = page.locator(FIELD);
      await expect(field).toBeVisible();
      // Foreground sits above the grid and rings, and its nodes are opaque so labels stay crisp.
      const layered = await field.evaluate((el) => {
        const fg = el.lastElementChild as HTMLElement;
        const model = el.querySelector("[data-endpoint='model']") as HTMLElement;
        return { z: getComputedStyle(fg).zIndex, position: getComputedStyle(fg).position, bg: getComputedStyle(model).backgroundColor };
      });
      expect(parseInt(layered.z, 10)).toBeGreaterThanOrEqual(1);
      expect(layered.position).toBe("relative");
      expect(layered.bg).not.toBe("rgba(0, 0, 0, 0)");
      const grid = await field.locator(".etched-grid").evaluate((el) => getComputedStyle(el).backgroundImage);
      expect(grid).toContain("repeating-linear-gradient");
      for (const text of ["Trained model", "Robot controller", "Release identity", "Evaluation evidence"]) {
        await expect(field.getByText(text, { exact: true })).toBeVisible();
      }
      const toggleHeight = await page.locator("[data-motion-toggle]").evaluate((el) => el.getBoundingClientRect().height);
      expect(toggleHeight, `motion control target at ${width}`).toBeGreaterThanOrEqual(44);
    }
  });

  test("hero copy, CTAs, contact and metadata are unchanged", async ({ page, request }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Deploy AI models to real robots.");
    await expect(page.locator("#top").getByRole("link", { name: "Discuss your deployment" })).toHaveAttribute("href", "#contact");
    await expect(page.locator("#top").getByRole("link", { name: "Explore the workflow" })).toHaveAttribute("href", "#workflow");
    await expect(page.locator("[data-contact-link]")).toHaveAttribute("href", /^mailto:founders@deployconvoy\.com\?subject=/);
    const html = await (await request.get("/")).text();
    expect(html).toContain("<title>Convoy | AI Model Deployment for Robots</title>");
    expect(html).toContain('<link rel="canonical" href="https://deployconvoy.com/"/>');
    expect(html).toContain("application/ld+json");
    expect(html).toContain('property="og:image" content="https://deployconvoy.com/share/');
  });
});

test.describe("etched field on a phone", () => {
  test.use({ viewport: { width: 393, height: 852 }, hasTouch: true, isMobile: true, deviceScaleFactor: 2 });

  // One CDP session per test: Chromium tracks the touch sequence per session.
  type Touch = (type: "touchStart" | "touchMove" | "touchEnd" | "touchCancel", points: { x: number; y: number }[]) => Promise<void>;
  async function touchInput(page: Page): Promise<Touch> {
    const cdp = await page.context().newCDPSession(page);
    return async (type, points) => {
      await cdp.send("Input.dispatchTouchEvent", { type, touchPoints: points.map((p, id) => ({ x: p.x, y: p.y, id })) });
    };
  }

  test("a short tap starts a wave at the tap point; a drag, an out-and-back drag, a slow press and a cancelled gesture start nothing; scrolling still works", async ({ page }) => {
    await page.goto("/");
    const field = page.locator(FIELD);
    await field.scrollIntoViewIfNeeded();
    await expect(field).toHaveAttribute("data-etched-field", "active");
    const touch = await touchInput(page);
    const box = await fieldBox(page);
    const tapAt = { x: box.x + 40, y: box.y + 50 };
    await touch("touchStart", [tapAt]);
    await touch("touchEnd", []);
    await expect(page.locator(RINGS)).toHaveCount(3);
    const origins = await ringOrigins(page.locator(RINGS));
    for (const o of origins) {
      expect(Math.abs(o.x - 40)).toBeLessThanOrEqual(1);
      expect(Math.abs(o.y - 50)).toBeLessThanOrEqual(1);
    }
    // Every gesture below must start no new wave. Waves are compared by id, since the tap's rings fade on their own.
    const seen = new Set(await waves(page));
    const newWaves = async () => (await waves(page)).filter((id) => !seen.has(id));
    // A drag that moves past the threshold, even if it comes back to the start.
    const start = { x: box.x + 200, y: box.y + 120 };
    await touch("touchStart", [start]);
    await touch("touchMove", [{ x: start.x + 30, y: start.y }]);
    await touch("touchMove", [start]);
    await touch("touchEnd", []);
    await page.waitForTimeout(150);
    expect(await newWaves(), "out-and-back drag starts nothing").toEqual([]);
    // A slow press.
    await touch("touchStart", [{ x: box.x + 220, y: box.y + 140 }]);
    await page.waitForTimeout(400);
    await touch("touchEnd", []);
    await page.waitForTimeout(150);
    expect(await newWaves(), "slow press starts nothing").toEqual([]);
    // A cancelled gesture.
    await touch("touchStart", [{ x: box.x + 240, y: box.y + 160 }]);
    await touch("touchCancel", []);
    await touch("touchStart", [{ x: box.x + 240, y: box.y + 160 }]);
    await touch("touchMove", [{ x: box.x + 240, y: box.y + 100 }]);
    await touch("touchEnd", []);
    await page.waitForTimeout(150);
    expect(await newWaves(), "cancelled and scrolling gestures start nothing").toEqual([]);
    // Two fingers (a pinch) are never a tap, even when both lift quickly and without moving.
    await touch("touchStart", [{ x: box.x + 100, y: box.y + 100 }]);
    await touch("touchStart", [{ x: box.x + 100, y: box.y + 100 }, { x: box.x + 180, y: box.y + 140 }]);
    await touch("touchEnd", [{ x: box.x + 180, y: box.y + 140 }]);
    await touch("touchEnd", []);
    await page.waitForTimeout(150);
    expect(await newWaves(), "two fingers start nothing").toEqual([]);
    // The field never blocks scrolling: touch-action stays auto and a swipe over the field scrolls the page.
    expect(await field.evaluate((el) => getComputedStyle(el).touchAction)).toBe("auto");
    const y0 = await page.evaluate(() => window.scrollY);
    await touch("touchStart", [{ x: box.x + 150, y: box.y + 200 }]);
    for (let i = 1; i <= 8; i++) await touch("touchMove", [{ x: box.x + 150, y: box.y + 200 - i * 30 }]);
    await touch("touchEnd", []);
    await page.waitForTimeout(300);
    expect(await page.evaluate(() => window.scrollY), "a swipe over the field scrolls the page").toBeGreaterThan(y0);
    // No horizontal overflow on the phone.
    const { scrollWidth, clientWidth } = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
    expect(scrollWidth).toBeLessThanOrEqual(clientWidth);
  });
});
