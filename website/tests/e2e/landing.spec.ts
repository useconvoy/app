import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Checks against the production build. Each test states what it proves;
 * screenshots are written to tests/screenshots for review, and every
 * viewport asserts the document never scrolls sideways.
 */

const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 900 },
  { name: "desktop-1280", width: 1280, height: 800 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "mobile-390", width: 390, height: 844 },
  { name: "mobile-320", width: 320, height: 568 },
] as const;

async function noHorizontalOverflow(page: Page) {
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(scrollWidth, "document must not scroll sideways").toBeLessThanOrEqual(clientWidth);
}

test.describe("landing page", () => {
  test("renders the approved hero and metadata", async ({ page }) => {
    await page.goto("/");
    await expect(page).toHaveTitle("Convoy | AI Model Deployment for Robots");
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Deploy AI models to real robots.");
    await expect(page.getByText(/Convoy is building the runtime and release workflow/)).toBeVisible();
    await expect(page.getByText("Help shape the next robot deployment workflow.")).toBeVisible();
    await expect(page.getByText("Stage:")).toHaveCount(0);
    await expect(page.getByText(/preview/i)).toHaveCount(0);
    await expect(page.locator("header")).not.toContainText("Precision Release");
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", /^https:\/\/deployconvoy\.com\/?$/);
    await expect(page.locator('meta[property="og:title"]')).toHaveAttribute("content", "Deploy AI models to real robots.");
    const ogImage = await page.locator('meta[property="og:image"]').getAttribute("content");
    expect(ogImage).toContain("/opengraph-image");
    // The tag carries the absolute production URL; fetch the same path from the server under test.
    const image = await page.request.get(new URL(ogImage!).pathname);
    expect(image.status()).toBe(200);
    expect(image.headers()["content-type"]).toContain("image/png");
  });

  test("section order and headings follow the brief", async ({ page }) => {
    await page.goto("/");
    const headings = await page.locator("h2").allTextContents();
    expect(headings).toEqual([
      "A trained model is not a robot deployment.",
      "Package the system. Qualify the release.",
      "Designed around your robot’s execution path.",
      "Start with one model. One robot configuration. One clear deployment goal.",
      "Questions about Convoy",
      "Tell us about your next robot deployment.",
    ]);
    await expect(page.getByRole("heading", { level: 3, name: /^Package$/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: /^Qualify$/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: /^Release$/ })).toBeVisible();
    // The release envelope repeats: the full object in the hero, the identity strip in the workflow.
    expect(await page.locator("[data-envelope]").count()).toBeGreaterThanOrEqual(2);
    await expect(page.locator("[data-envelope]").filter({ hasText: "Robot-policy release" })).toHaveCount(1);
    expect(await page.locator("[data-envelope]").filter({ hasText: "Release identity" }).count()).toBeGreaterThanOrEqual(1);
    await expect(page.getByText(/v0\.3|proposed|illustrative|not an API/i)).toHaveCount(0);
    // Six FAQ boundaries, the first open.
    const faq = page.locator("#faq details");
    await expect(faq).toHaveCount(6);
    await expect(faq.first()).toHaveAttribute("open", "");
  });

  test("every on-page anchor resolves to an element", async ({ page }) => {
    await page.goto("/");
    const targets = await page.locator('a[href^="#"]').evaluateAll((links) =>
      Array.from(new Set(links.map((a) => (a as HTMLAnchorElement).getAttribute("href")!))),
    );
    expect(targets.length).toBeGreaterThan(3);
    for (const target of targets) {
      await expect(page.locator(target), `anchor ${target} must exist`).toHaveCount(1);
    }
    // Following the primary action lands on the contact section.
    await page.getByRole("navigation", { name: "Main" }).first().getByRole("link", { name: "Discuss your deployment" }).click();
    await expect(page).toHaveURL(/#contact$/);
    await expect(page.locator("#contact")).toBeInViewport();
  });

  test("skip link and keyboard reach the content and the form", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to content" });
    await expect(skip).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#main")).toBeFocused();
    // FAQ disclosures toggle from the keyboard.
    const second = page.locator("#faq details").nth(1);
    await second.locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(second).toHaveAttribute("open", "");
    await page.keyboard.press("Space");
    await expect(second).not.toHaveAttribute("open", "");
  });

  test("mobile menu exposes state, closes on Escape, and restores focus", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const button = page.getByRole("button", { name: "Menu" });
    await expect(button).toBeVisible();
    await expect(button).toHaveAttribute("aria-expanded", "false");
    await button.click();
    await expect(page.getByRole("button", { name: "Close" })).toHaveAttribute("aria-expanded", "true");
    const menu = page.locator("[data-mobile-menu]");
    const menuLink = menu.getByRole("link", { name: "How it works" });
    await expect(menu).toBeVisible();
    await expect(menuLink).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByRole("button", { name: "Menu" })).toBeFocused();
    await expect(menu).toBeHidden();
    // A menu link navigates to its section and closes the menu.
    await page.getByRole("button", { name: "Menu" }).click();
    await menu.getByRole("link", { name: "Design partnership" }).click();
    await expect(page).toHaveURL(/#partnership$/);
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
  });

  test("contact block opens the visitor's email app with the subject filled in", async ({ page }) => {
    await page.goto("/#contact");
    const link = page.locator("[data-contact-link]");
    await expect(link).toBeVisible();
    const href = (await link.getAttribute("href"))!;
    expect(href.startsWith("mailto:aws@deployconvoy.com?")).toBe(true);
    const params = new URLSearchParams(href.slice(href.indexOf("?") + 1));
    expect(params.get("subject")).toBe("Convoy deployment inquiry");
    expect(params.get("body")).toContain("Model:");
    expect(params.get("body")).toContain("Robot configuration:");
    expect(params.get("body")).toContain("Deployment challenge:");
    // The address is visible, selectable text with its own plain mailto.
    const address = page.locator("[data-contact-address]");
    await expect(address).toHaveText("aws@deployconvoy.com");
    await expect(address).toHaveAttribute("href", "mailto:aws@deployconvoy.com");
    await expect(page.getByText("Opens your email app")).toBeVisible();
    // No form, no fields, nothing to submit or store.
    await expect(page.locator("form")).toHaveCount(0);
    await expect(page.getByText(/received|sending is not available/i)).toHaveCount(0);
    // Every primary action on the page lands on the contact section.
    const ctas = page.getByRole("link", { name: "Discuss your deployment" });
    for (const href of await ctas.evaluateAll((links) => links.map((a) => a.getAttribute("href")))) {
      expect(href).toBe("#contact");
    }
  });

  test("legacy marketing paths redirect home and the old console paths do not", async ({ page }) => {
    for (const path of ["/platform", "/solutions", "/demo", "/privacy"]) {
      const response = await page.request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(308);
      expect(response.headers()["location"], path).toBe("/");
    }
    for (const path of ["/app", "/sign-in", "/api/auth/workos"]) {
      const response = await page.request.get(path, { maxRedirects: 0 });
      expect(response.status(), path).toBe(404);
    }
  });

  test("reduced motion renders the complete static page with no transitions", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const duration = await page.locator("#faq details summary svg").first().evaluate((el) => getComputedStyle(el).transitionDuration);
    expect(parseFloat(duration)).toBeLessThanOrEqual(0.0001);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.locator("#execution figure")).toBeVisible();
    await page.screenshot({ path: "tests/screenshots/desktop-1440-reduced-motion.png", fullPage: true });
  });

  test("interactive controls meet 44px targets on mobile and desktop", async ({ page }) => {
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      if (width < 900) await page.getByRole("button", { name: "Menu" }).click();
      const small = await page
        .locator("a.btn, button, header nav a, footer nav a, summary, [data-contact-link]")
        .evaluateAll((els) =>
          els
            .filter((el) => (el as HTMLElement).offsetParent !== null)
            .map((el) => ({ text: (el.textContent || "").trim().slice(0, 40), h: el.getBoundingClientRect().height }))
            .filter((m) => m.h < 44),
        );
      expect(small, `controls under 44px at ${width}`).toEqual([]);
    }
  });

  test("diagrams reflow to a vertical, ordered structure on mobile with the boundary intact", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    // Execution path: nodes keep their flow order top to bottom, no sideways scroll.
    const nodes = page.locator('#execution ol[aria-label="Execution path nodes"] > li:not([aria-hidden])');
    await expect(nodes).toHaveCount(5);
    const boxes = await nodes.evaluateAll((els) => els.map((el) => el.getBoundingClientRect()).map((r) => ({ top: r.top, width: r.width })));
    for (let i = 1; i < boxes.length; i += 1) expect(boxes[i].top, "nodes stack vertically in order").toBeGreaterThan(boxes[i - 1].top);
    for (const box of boxes) expect(box.width).toBeGreaterThan(250);
    await expect(nodes.nth(0)).toContainText("Sensors");
    await expect(nodes.nth(4)).toContainText("Robot controller");
    await expect(nodes.nth(4)).toContainText("outside Convoy");
    await expect(page.locator("[data-scope-text]")).toBeVisible();
    await expect(page.locator("[data-scope-text]")).toHaveText("Convoy runtime scope");
    // Node labels stay readable on mobile: 16px labels, 13px metadata.
    const sizes = await nodes.first().locator("span").evaluateAll((els) => els.map((el) => parseFloat(getComputedStyle(el).fontSize)));
    expect(sizes[0]).toBeGreaterThanOrEqual(16);
    expect(sizes[1]).toBeGreaterThanOrEqual(13);
    // And on desktop the same scope text is exposed to assistive technology.
    await page.setViewportSize({ width: 1440, height: 900 });
    const exposed = await page.locator("[data-scope-text]").evaluate((el) => el.closest("[aria-hidden='true']") === null && getComputedStyle(el).display !== "none");
    expect(exposed, "scope text stays in the accessibility tree on desktop").toBe(true);
    // Hero: model, release, controller read top to bottom.
    const hero = page.locator("#top figure");
    const order = await hero.locator("[data-endpoint], [data-envelope]").evaluateAll((els) =>
      els.map((el) => ({ kind: el.getAttribute("data-endpoint") ?? "release", top: el.getBoundingClientRect().top })),
    );
    expect(order.map((o) => o.kind)).toEqual(["model", "release", "robot"]);
    expect(order[0].top).toBeLessThan(order[1].top);
    expect(order[1].top).toBeLessThan(order[2].top);
    await noHorizontalOverflow(page);
  });

  test("200% zoom reflow and a narrow 320px column keep the page readable", async ({ page }) => {
    // 1440px at 200% zoom is a 720px CSS viewport; 320px is the reflow floor.
    for (const width of [720, 320]) {
      await page.setViewportSize({ width, height: 800 });
      await page.goto("/");
      await page.locator("details:not([open]) > summary").evaluateAll((summaries) =>
        summaries.forEach((s) => ((s.parentElement as HTMLDetailsElement).open = true)),
      );
      await noHorizontalOverflow(page);
      const address = page.locator("[data-contact-address]");
      const fits = await address.evaluate((el) => {
        const r = el.getBoundingClientRect();
        const c = el.closest("div")!.getBoundingClientRect();
        return r.right <= c.right + 1 && r.left >= c.left - 1;
      });
      expect(fits, `contact address stays inside its block at ${width}`).toBe(true);
    }
  });

  for (const viewport of VIEWPORTS) {
    test(`no accessibility violations and no sideways scroll at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/");
      await noHorizontalOverflow(page);
      // Open every disclosure so hidden content is checked and photographed too.
      await page.locator("details:not([open]) > summary").evaluateAll((summaries) =>
        summaries.forEach((s) => (s.parentElement as HTMLDetailsElement).open = true),
      );
      await noHorizontalOverflow(page);
      const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag22aa"]).analyze();
      expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
      await page.screenshot({ path: `tests/screenshots/${viewport.name}-expanded.png`, fullPage: true });
      await page.reload();
      await page.screenshot({ path: `tests/screenshots/${viewport.name}.png`, fullPage: true });
    });
  }
});
