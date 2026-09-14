import { expect, test } from "@playwright/test";

const FIELD = "[data-hero-motion]";
const FOCUS = ".hero-focus";
const LAMP = ".hero-lamp";

test.describe("illustrated hero motion", () => {
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

  test("mouse movement uses one small focus marker without moving content or leaving a trail", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "active");
    await expect(page.locator("#top")).not.toContainText("FIG. 01");
    await expect(page.locator(".hero-art-label")).toHaveCount(0);
    const heading = await page.locator("h1").boundingBox();
    const initialNodes = await page.locator(`${FIELD} *`).count();
    for (let i = 0; i < 10; i++) {
      const x = 700 + i * 30, y = 240 + (i % 3) * 40;
      await page.mouse.move(x, y);
      await expect(page.locator(FIELD)).toHaveAttribute("data-hovering", "true");
      const box = (await page.locator(FOCUS).boundingBox())!;
      expect(box.width).toBeLessThanOrEqual(40);
      expect(box.height).toBeLessThanOrEqual(40);
      expect(Math.abs(box.x + box.width / 2 - x)).toBeLessThan(1);
      expect(Math.abs(box.y + box.height / 2 - y)).toBeLessThan(1);
    }
    expect(await page.locator(`${FIELD} *`).count()).toBe(initialNodes);
    expect(await page.locator("h1").boundingBox()).toEqual(heading);
    await page.mouse.move(4, 4);
    await expect(page.locator(FIELD)).not.toHaveAttribute("data-hovering", "true");
    await expect(page.locator(FOCUS)).toHaveCSS("opacity", "0");
  });

  test("the lamp softly changes brightness and stays inside the artwork after responsive resizing", async ({ page }) => {
    await page.goto("/");
    const lamp = page.locator(LAMP);
    await expect(page.locator(FIELD)).toHaveAttribute("data-lamp-ready", "true");
    await expect(lamp).toBeVisible();
    const samples: number[] = [];
    for (let i = 0; i < 8; i++) {
      samples.push(await lamp.evaluate((el) => parseFloat(getComputedStyle(el).opacity)));
      await page.waitForTimeout(600);
    }
    expect(Math.max(...samples) - Math.min(...samples)).toBeGreaterThan(.1);
    for (const size of [{width:1440,height:1000}, {width:768,height:1000}, {width:390,height:844}, {width:844,height:390}, {width:932,height:430}]) {
      await page.setViewportSize(size);
      await expect.poll(async () => {
        const glow = (await lamp.boundingBox())!;
        const art = (await page.locator(".hero-art").boundingBox())!;
        return glow.x >= art.x && glow.y >= art.y && glow.x + glow.width <= art.x + art.width && glow.y + glow.height <= art.y + art.height;
      }).toBe(true);
    }
  });

  test("pause persists across reload, reduced motion wins, and resuming re-enables interaction", async ({ page }) => {
    await page.goto("/");
    await page.locator("[data-motion-toggle]").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "paused");
    await expect(page.locator(FIELD)).not.toHaveAttribute("data-hovering", "true");
    await expect(page.locator(LAMP)).toHaveCSS("animation-name", "none");
    await page.reload();
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "paused");
    await page.emulateMedia({reducedMotion: "reduce"});
    await page.locator("[data-motion-toggle]").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "reduced");
    await page.locator("header a[aria-label='Convoy home']").click();
    await page.mouse.move(300, 200);
    await expect(page.locator(FIELD)).not.toHaveAttribute("data-hovering", "true");
    await expect(page.locator(LAMP)).toHaveCSS("animation-name", "none");
    await page.emulateMedia({reducedMotion: "no-preference"});
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "active");
    await page.mouse.move(360, 240);
    await expect(page.locator(FIELD)).toHaveAttribute("data-hovering", "true");
  });

  test("offscreen and hidden pages suspend motion; resize clears the focus marker", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "active");
    await expect(page.locator(FIELD)).toHaveAttribute("data-lamp-ready", "true");
    await page.mouse.move(300, 200);
    await expect(page.locator(FIELD)).toHaveAttribute("data-hovering", "true");
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.locator(FIELD)).not.toHaveAttribute("data-hovering", "true");
    await page.mouse.move(350, 220);
    await expect(page.locator(FIELD)).toHaveAttribute("data-hovering", "true");
    await page.locator("#contact").scrollIntoViewIfNeeded();
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "offscreen");
    await expect(page.locator(FIELD)).not.toHaveAttribute("data-hovering", "true");
    await expect(page.locator(LAMP)).toHaveCSS("animation-name", "none");
    await page.locator("header a[aria-label='Convoy home']").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "active");
    await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "hidden");
    await expect(page.locator(FIELD)).not.toHaveAttribute("data-hovering", "true");
    await expect(page.locator(LAMP)).toHaveCSS("animation-name", "none");
  });

  test("blocked storage does not prevent the motion control working", async ({page}) => {
    await page.addInitScript(() => {
      Storage.prototype.setItem = () => { throw new Error("blocked"); };
      Storage.prototype.getItem = () => { throw new Error("blocked"); };
      Storage.prototype.removeItem = () => { throw new Error("blocked"); };
    });
    await page.goto("/");
    await page.locator("[data-motion-toggle]").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "paused");
    await page.locator("[data-motion-toggle]").click();
    await page.locator("header a[aria-label='Convoy home']").click();
    await expect(page.locator(FIELD)).toHaveAttribute("data-hero-motion", "active");
  });
});

test.describe("touch hero", () => {
  test.use({ viewport: {width: 393, height: 852}, hasTouch: true, isMobile: true });
  test("touch scrolling works across the decorative surface with no focus marker", async ({page}) => {
    await page.goto("/");
    const field = page.locator(FIELD);
    expect(await field.evaluate((el) => getComputedStyle(el).touchAction)).toBe("auto");
    expect(await page.locator(FOCUS).evaluate((el) => getComputedStyle(el).pointerEvents)).toBe("none");
    const session = await page.context().newCDPSession(page);
    await session.send("Input.dispatchTouchEvent", {type: "touchStart", touchPoints: [{x: 180, y: 720}]});
    for (let i = 1; i <= 8; i++) await session.send("Input.dispatchTouchEvent", {type: "touchMove", touchPoints: [{x: 180, y: 720 - i * 45}]});
    await session.send("Input.dispatchTouchEvent", {type: "touchEnd", touchPoints: []});
    await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(100);
    await expect(field).not.toHaveAttribute("data-hovering", "true");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
});
