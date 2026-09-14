import { expect, test } from "@playwright/test";

const FIELD = "[data-pixel-field]";
const PIXELS = ".field-pixel";

test.describe("illustrated hero and pixel field", () => {
  test("the real asset loads eagerly and both actions remain usable", async ({ page, request }) => {
    await page.goto("/");
    const art = page.locator(".hero-art");
    await expect(art).toHaveAttribute("alt", /humanoid robot/);
    expect(await art.evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBe(1536);
    const response = await request.get("/images/convoy-humanoid-illustrated.webp");
    expect(response.ok()).toBe(true);
    expect(response.headers()["content-type"]).toContain("image/webp");
    expect((await response.body()).byteLength).toBeLessThan(350_000);
    await expect(page.locator("#top").getByRole("link", { name: "Discuss your deployment" })).toHaveAttribute("href", "#contact");
    await page.locator("#top").getByRole("link", { name: "Explore the workflow" }).click();
    await expect(page.locator("#workflow")).toBeInViewport();
  });

  test("mouse movement leaves a bounded, snapped color trail that fades without moving content", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "active");
    const heading = await page.locator("h1").boundingBox();
    await page.mouse.move(300, 200);
    await expect.poll(() => page.locator(PIXELS).count()).toBeGreaterThan(0);
    const size = await page.locator(PIXELS).first().boundingBox();
    expect(size?.width, "hover squares stay fine at five CSS pixels").toBe(5);
    expect(size?.height).toBe(5);
    const cells = await page.locator(PIXELS).evaluateAll((pixels) => pixels.map((pixel) => ({x: parseFloat((pixel as HTMLElement).style.left), y: parseFloat((pixel as HTMLElement).style.top)})));
    expect(cells.every((p) => p.x % 8 === 0 && p.y % 8 === 0)).toBe(true);
    for (let i = 0; i < 18; i++) {
      await page.mouse.move(200 + i * 30, 220 + (i % 3) * 50);
      await page.waitForTimeout(70);
      expect(await page.locator(PIXELS).count()).toBeLessThanOrEqual(180);
    }
    await expect(page.locator(FIELD)).toHaveAttribute("data-hovering", "true");
    expect(await page.locator("h1").boundingBox()).toEqual(heading);
    await page.mouse.move(4, 4);
    await expect(page.locator(FIELD)).not.toHaveAttribute("data-hovering", "true");
    // Keep the mouse stationary inside after clearing so ambient decoration cannot mask trail expiry.
    await page.setViewportSize({width: 1430, height: 900});
    await page.mouse.move(200, 210);
    await expect.poll(() => page.locator(PIXELS).count()).toBeGreaterThan(0);
    await expect(page.locator(PIXELS)).toHaveCount(0, {timeout: 3000});
  });

  test("pause persists across reload, reduced motion wins, and resuming re-enables interaction", async ({ page }) => {
    await page.goto("/");
    await page.locator("[data-motion-toggle]").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "paused");
    await expect(page.locator(PIXELS)).toHaveCount(0);
    await page.reload();
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "paused");
    await page.emulateMedia({reducedMotion: "reduce"});
    await page.locator("[data-motion-toggle]").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "reduced");
    await page.locator("header a[aria-label='Convoy home']").click();
    await page.mouse.move(300, 200);
    await expect(page.locator(PIXELS)).toHaveCount(0);
    await page.emulateMedia({reducedMotion: "no-preference"});
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "active");
    await page.mouse.move(360, 240);
    await expect.poll(() => page.locator(PIXELS).count()).toBeGreaterThan(0);
  });

  test("offscreen and hidden pages clear motion; resize resets the trail", async ({ page }) => {
    await page.goto("/");
    await page.mouse.move(300, 200);
    await expect.poll(() => page.locator(PIXELS).count()).toBeGreaterThan(0);
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.locator(PIXELS)).toHaveCount(0);
    await page.mouse.move(350, 220);
    await expect.poll(() => page.locator(PIXELS).count()).toBeGreaterThan(0);
    await page.locator("#contact").scrollIntoViewIfNeeded();
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "offscreen");
    await expect(page.locator(PIXELS)).toHaveCount(0);
    await page.locator("header a[aria-label='Convoy home']").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "active");
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "hidden");
    await expect(page.locator(PIXELS)).toHaveCount(0);
  });

  test("blocked storage does not prevent the motion control working", async ({page}) => {
    await page.addInitScript(() => {
      Storage.prototype.setItem = () => { throw new Error("blocked"); };
      Storage.prototype.getItem = () => { throw new Error("blocked"); };
      Storage.prototype.removeItem = () => { throw new Error("blocked"); };
    });
    await page.goto("/");
    await page.locator("[data-motion-toggle]").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "paused");
    await page.locator("[data-motion-toggle]").click();
    await page.locator("header a[aria-label='Convoy home']").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-pixel-field", "active");
  });
});

test.describe("touch hero", () => {
  test.use({ viewport: {width: 393, height: 852}, hasTouch: true, isMobile: true });
  test("touch scrolling works across the decorative surface with no pointer trail", async ({page}) => {
    await page.goto("/");
    const field = page.locator(FIELD);
    expect(await field.evaluate((el) => getComputedStyle(el).touchAction)).toBe("auto");
    expect(await page.locator(".pixel-trail").evaluate((el) => getComputedStyle(el).pointerEvents)).toBe("none");
    const session = await page.context().newCDPSession(page);
    await session.send("Input.dispatchTouchEvent", {type: "touchStart", touchPoints: [{x: 180, y: 720}]});
    for (let i = 1; i <= 8; i++) await session.send("Input.dispatchTouchEvent", {type: "touchMove", touchPoints: [{x: 180, y: 720 - i * 45}]});
    await session.send("Input.dispatchTouchEvent", {type: "touchEnd", touchPoints: []});
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
    await expect(field).not.toHaveAttribute("data-hovering", "true");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
