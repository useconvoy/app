import { AxeBuilder } from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

/**
 * Checks against the production build. Each test states what it proves;
 * screenshots are written to tests/screenshots for review, and every
 * viewport asserts the document never scrolls sideways.
 */

const VIEWPORTS = [
  { name: "desktop-1440", width: 1440, height: 900 },
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
    await expect(page.getByText("Stage: In development")).toBeVisible();
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
      "A few important boundaries.",
      "What does your next robot deployment need?",
    ]);
    await expect(page.getByRole("heading", { level: 3, name: /^Package$/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: /^Qualify$/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: /^Release$/ })).toBeVisible();
    // The release envelope repeats with the same illustrative identifier.
    expect(await page.locator("[data-envelope]").count()).toBeGreaterThanOrEqual(2);
    const ids = await page.locator("[data-envelope]").filter({ hasText: "release v0.3" }).count();
    expect(ids).toBeGreaterThanOrEqual(2);
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

  test("contact form validates, announces errors, keeps values, and never claims receipt", async ({ page }) => {
    await page.goto("/#contact");
    const form = page.locator("form");
    await expect(page.getByText("Sending is not available yet")).toBeVisible();
    await form.getByRole("button", { name: "Send deployment details" }).click();
    const alert = form.getByRole("alert");
    await expect(alert).toBeVisible();
    await expect(alert).toBeFocused();
    await expect(alert).toContainText("Work email");
    await expect(alert).toContainText("Company or team");
    await expect(form.locator("#contact-email")).toHaveAttribute("aria-invalid", "true");

    await form.locator("#contact-email").fill("engineer@example.com");
    await form.locator("#contact-company").fill("Example Robotics");
    await form.locator("#contact-blocker").fill("A trained pick policy; the action interface changed with the last model release.");
    await form.locator("summary", { hasText: "Optional details" }).click();
    await form.locator("#contact-hardware").fill("Illustrative arm and onboard compute");
    await form.getByRole("button", { name: "Send deployment details" }).click();

    const status = form.getByRole("status");
    await expect(status).toContainText("were not sent");
    await expect(status).toContainText("Nothing was stored");
    await expect(status).toBeFocused();
    await expect(page.getByText("have been received")).toHaveCount(0);
    // Everything typed is still there.
    await expect(form.locator("#contact-email")).toHaveValue("engineer@example.com");
    await expect(form.locator("#contact-company")).toHaveValue("Example Robotics");
    await expect(form.locator("#contact-blocker")).toHaveValue(/action interface changed/);
    await expect(form.locator("#contact-hardware")).toHaveValue("Illustrative arm and onboard compute");
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
