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
  { name: "desktop-1024", width: 1024, height: 768 },
  { name: "tablet-768", width: 768, height: 1024 },
  { name: "landscape-844x390", width: 844, height: 390 },
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
    await expect(page.getByText("Stage:")).toHaveCount(0);
    await expect(page.getByText(/preview/i)).toHaveCount(0);
    await expect(page.locator("header")).not.toContainText("Precision Release");
    // Head tags, icons, JSON-LD and the share card are covered in metadata.spec.ts.
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute("href", "https://deployconvoy.com/");
  });

  test("section order and headings follow the brief", async ({ page }) => {
    await page.goto("/");
    const headings = await page.locator("h2").allTextContents();
    expect(headings).toEqual([
      "A trained model is not a robot deployment.",
      "Package. Qualify. Release.",
      "Designed around your robot’s execution path.",
      "Start with one deployment.",
      "Questions about Convoy",
      "Tell us about your next robot deployment.",
    ]);
    await expect(page.getByRole("heading", { level: 3, name: /^Package$/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: /^Qualify$/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 3, name: /^Release$/ })).toBeVisible();
    // One release envelope, in the hero, with all six parts as rows and the examples behind one disclosure.
    const envelope = page.locator("[data-envelope]");
    await expect(envelope).toHaveCount(1);
    await expect(envelope).toContainText("Robot-policy release");
    await expect(envelope.locator("[data-field]")).toHaveCount(6);
    for (const part of ["Release identity", "Model assets", "Input / action processing", "Runtime", "Target configuration", "Evaluation evidence"]) {
      await expect(envelope.getByText(part, { exact: true })).toBeVisible();
    }
    const details = page.locator("#top details");
    await expect(details).toHaveCount(1);
    await expect(details).not.toHaveAttribute("open", "");
    await expect(page.locator("[data-release-details]")).toBeHidden();
    await details.locator("summary").focus();
    await page.keyboard.press("Enter");
    await expect(page.locator("[data-release-details]")).toBeVisible();
    await expect(page.locator("[data-release-details]")).toContainText("policy weights");
    await expect(page.locator("[data-release-details]")).toContainText("what executes it");
    // Ownership stays visible in the compact figure without opening anything.
    await expect(page.locator("[data-endpoint='model']")).toContainText("your team");
    await expect(page.locator("[data-endpoint='robot']")).toContainText("outside Convoy");
    await page.keyboard.press("Space");
    await expect(page.locator("[data-release-details]")).toBeHidden();
    await expect(page.getByText(/v0\.3|proposed|NOT AN API|Precision Release|illustrative ID/)).toHaveCount(0);
    await expect(page.locator("[data-envelope]").first().getByText("Release identity")).toBeVisible();
    // Six FAQ items; availability comes first and starts open.
    const faq = page.locator("#faq details");
    await expect(faq).toHaveCount(6);
    await expect(faq.first()).toHaveAttribute("open", "");
    await expect(faq.first()).toContainText("What can I use today?");
    await expect(faq.first()).toContainText("Convoy is in development");
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

  test("sticky header marks the current section and never covers anchor targets", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const header = page.locator("header");
    expect(await header.evaluate((el) => getComputedStyle(el).position)).toBe("sticky");
    const headerHeight = await header.evaluate((el) => el.getBoundingClientRect().height);
    const padding = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop));
    expect(padding).toBeGreaterThanOrEqual(headerHeight);
    await page.locator("header nav").first().getByRole("link", { name: "How it works" }).click();
    await expect(page).toHaveURL(/#workflow$/);
    const top = await page.locator("#workflow").evaluate((el) => el.getBoundingClientRect().top);
    expect(top, "section starts below the sticky header").toBeGreaterThanOrEqual(headerHeight);
    await expect(page.locator("header nav").first().getByRole("link", { name: "How it works" })).toHaveAttribute("aria-current", "true");
    await expect(page.locator("header nav").first().getByRole("link", { name: "Contact" })).not.toHaveAttribute("aria-current", "true");
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
    // A pointer-down outside the menu closes it.
    await page.getByRole("button", { name: "Menu" }).click();
    await expect(menu).toBeVisible();
    await page.mouse.click(200, 700);
    await expect(menu).toBeHidden();
    await expect(page.getByRole("button", { name: "Menu" })).toHaveAttribute("aria-expanded", "false");
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
    expect(href.startsWith("mailto:founders@deployconvoy.com?")).toBe(true);
    const params = new URLSearchParams(href.slice(href.indexOf("?") + 1));
    expect(params.get("subject")).toBe("Convoy deployment inquiry");
    expect(params.get("body")).toContain("Model:");
    expect(params.get("body")).toContain("Robot configuration:");
    expect(params.get("body")).toContain("Deployment challenge:");
    // The address is visible, selectable text with its own plain mailto.
    const address = page.locator("[data-contact-address]");
    await expect(address).toHaveText("founders@deployconvoy.com");
    await expect(address).toHaveAttribute("href", "mailto:founders@deployconvoy.com");
    await expect(page.getByText("Opens your email app")).toBeVisible();
    // The action comes before the guidance in reading order.
    const order = await page.locator("#contact").evaluate((el) => {
      const link = el.querySelector("[data-contact-link]")!.getBoundingClientRect().top;
      const guide = Array.from(el.querySelectorAll("p")).find((p) => p.textContent?.trim() === "Helpful to include")!.getBoundingClientRect().top;
      return link < guide;
    });
    expect(order).toBe(true);
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
    const nodes = page.locator("#execution [data-node]");
    await expect(nodes).toHaveCount(5);
    const boxes = await nodes.evaluateAll((els) => els.map((el) => el.getBoundingClientRect()).map((r) => ({ top: r.top, width: r.width })));
    for (let i = 1; i < boxes.length; i += 1) expect(boxes[i].top, "nodes stack vertically in order").toBeGreaterThan(boxes[i - 1].top);
    for (const box of boxes) expect(box.width).toBeGreaterThan(250);
    await expect(nodes.nth(0)).toContainText("Sensors");
    await expect(nodes.nth(4)).toContainText("Robot controller");
    await expect(nodes.nth(4)).toContainText("safety system");
    // The Convoy runtime boundary is a labelled group holding exactly the three Convoy nodes, visible at 390 and 1440.
    for (const width of [390, 1440]) {
      await page.setViewportSize({ width, height: 900 });
      const boundary = page.getByRole("group", { name: "Convoy runtime boundary" });
      await expect(boundary).toBeVisible();
      await expect(boundary.locator("[data-node]")).toHaveCount(3);
      await expect(boundary).toContainText("Input processing");
      await expect(boundary).toContainText("Action processing");
      await expect(boundary).not.toContainText("Robot controller");
      await expect(boundary.getByText("Convoy runtime boundary")).toBeVisible();
      expect(await boundary.evaluate((el) => el.closest("[aria-hidden='true']") === null)).toBe(true);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    // Node labels stay readable on mobile: 16px labels, 14px metadata.
    const sizes = await nodes.first().locator("span").evaluateAll((els) => els.map((el) => parseFloat(getComputedStyle(el).fontSize)));
    expect(sizes[0]).toBeGreaterThanOrEqual(16);
    expect(sizes[1]).toBeGreaterThanOrEqual(14);
    // The figure carries a written description of the same relationships.
    await expect(page.locator("#execution-diagram-description")).toContainText("Outside Convoy: Sensors and Robot controller");
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
      // The address is one contiguous string; if it wraps, it wraps before the @ and the domain stays whole.
      await expect(address).toHaveText("founders@deployconvoy.com");
      await expect(address).toHaveAttribute("href", "mailto:founders@deployconvoy.com");
      const lines = await address.evaluate((el) => {
        const rows = (node: Element) => new Set(Array.from(node.getClientRects()).map((r) => Math.round(r.top))).size;
        return { address: rows(el), domain: rows(el.querySelector("[data-contact-domain]")!) };
      });
      expect(lines.domain, `domain never splits at ${width}`).toBe(1);
      expect(lines.address, `address takes at most two lines at ${width}`).toBeLessThanOrEqual(2);
    }
  });

  test("execution trace: complete at first paint, plays once in view, replays on request, cleans up", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const trace = page.locator("[data-trace]");
    // The whole diagram is static and complete before anything moves.
    await expect(trace).toHaveAttribute("data-trace", "idle");
    const overlays = trace.locator(".trace-overlay");
    await expect(overlays).toHaveCount(4);
    const base = await trace.locator(".trace-edge svg > path:first-child").evaluateAll((els) => els.map((el) => getComputedStyle(el).opacity));
    expect(base).toEqual(["1", "1", "1", "1"]);
    expect(await overlays.evaluateAll((els) => els.map((el) => getComputedStyle(el).opacity))).toEqual(["0", "0", "0", "0"]);
    await expect(page.getByRole("group", { name: "Convoy runtime boundary" })).toHaveCount(1);
    // One autoplay when the figure comes into view.
    await trace.scrollIntoViewIfNeeded();
    await expect(trace).toHaveAttribute("data-trace", "playing", { timeout: 3000 });
    await expect(trace).toHaveAttribute("data-trace", "done", { timeout: 5000 });
    // Leaving and returning does not play it again.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.waitForTimeout(300);
    await trace.scrollIntoViewIfNeeded();
    await page.waitForTimeout(700);
    await expect(trace).toHaveAttribute("data-trace", "done");
    // Pointer movement over the figure does not restart it.
    const box = (await trace.boundingBox())!;
    await page.mouse.move(box.x + 60, box.y + 60);
    await page.mouse.move(box.x + 400, box.y + 90, { steps: 8 });
    await page.waitForTimeout(250);
    await expect(trace).toHaveAttribute("data-trace", "done");
    // A real button replays it: click, Enter, Space.
    const button = page.locator("[data-trace-button]");
    await expect(button).toHaveText("Trace again");
    expect((await button.boundingBox())!.height).toBeGreaterThanOrEqual(48);
    await button.click();
    await expect(trace).toHaveAttribute("data-trace", "playing");
    await expect(trace).toHaveAttribute("data-trace", "done", { timeout: 5000 });
    await button.focus();
    await page.keyboard.press("Enter");
    await expect(trace).toHaveAttribute("data-trace", "playing");
    await expect(trace).toHaveAttribute("data-trace", "done", { timeout: 5000 });
    await page.keyboard.press("Space");
    await expect(trace).toHaveAttribute("data-trace", "playing");
    // Rapid repeats settle to done, once, without errors.
    await button.click();
    await button.click();
    await button.click();
    await expect(trace).toHaveAttribute("data-trace", "done", { timeout: 5000 });
    await page.waitForTimeout(400);
    await expect(trace).toHaveAttribute("data-trace", "done");
    // Hiding the document cancels a run in progress.
    await button.click();
    await expect(trace).toHaveAttribute("data-trace", "playing");
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await expect(trace).toHaveAttribute("data-trace", "idle");
    // Node labels are never focusable; only the button is.
    await expect(trace.locator("[data-node] [tabindex], [data-node] a, [data-node] button")).toHaveCount(0);
    expect(errors).toEqual([]);
  });

  test("execution trace in a short viewport: starts from the path's start, interruption never restarts it", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width: 320, height: 568 });
    await page.goto("/");
    const trace = page.locator("[data-trace]");
    const figureHeight = await trace.evaluate((el) => el.getBoundingClientRect().height);
    expect(figureHeight, "the figure is taller than this viewport").toBeGreaterThan(568);
    // Bring the start of the path into the viewport with room below it.
    await page.evaluate(() => {
      const start = document.querySelector("[data-trace-start]")!;
      window.scrollTo(0, start.getBoundingClientRect().top + window.scrollY - 120);
    });
    await expect(trace).toHaveAttribute("data-trace", "playing", { timeout: 3000 });
    // Scrolling fully away mid-run cancels it back to the static state.
    await page.evaluate(() => window.scrollTo(0, 0));
    await expect(trace).toHaveAttribute("data-trace", "idle", { timeout: 2000 });
    // Coming back does not restart it: the one autoplay was consumed.
    await page.evaluate(() => {
      const start = document.querySelector("[data-trace-start]")!;
      window.scrollTo(0, start.getBoundingClientRect().top + window.scrollY - 120);
    });
    await page.waitForTimeout(800);
    await expect(trace).toHaveAttribute("data-trace", "idle");
    // The explicit replay still works, and a rotation mid-run settles.
    const button = page.locator("[data-trace-button]");
    await button.scrollIntoViewIfNeeded();
    await button.click();
    await expect(trace).toHaveAttribute("data-trace", "playing");
    await page.setViewportSize({ width: 568, height: 320 });
    await expect(trace).toHaveAttribute("data-trace", "idle", { timeout: 2000 });
    await noHorizontalOverflow(page);
    expect(errors).toEqual([]);
  });

  test("execution trace lifecycle: cancellation before the scheduled frame wins, same-frame repeats collapse, manual play consumes autoplay", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const trace = page.locator("[data-trace]");
    await expect(trace).toHaveAttribute("data-trace", "idle");
    // Activate and hide the document synchronously, before the frame that would start playback.
    await page.evaluate(() => {
      (document.querySelector("[data-trace-button]") as HTMLButtonElement).click();
      Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.waitForTimeout(400);
    await expect(trace).toHaveAttribute("data-trace", "idle");
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.waitForTimeout(300);
    await expect(trace).toHaveAttribute("data-trace", "idle");
    // Three activations in the same frame produce one run that ends once and stays ended.
    await page.evaluate(() => {
      const button = document.querySelector("[data-trace-button]") as HTMLButtonElement;
      button.click();
      button.click();
      button.click();
    });
    await expect(trace).toHaveAttribute("data-trace", "playing");
    await expect(trace).toHaveAttribute("data-trace", "done", { timeout: 5000 });
    const flips = await trace.evaluate(
      (el) =>
        new Promise<number>((resolve) => {
          let count = 0;
          const observer = new MutationObserver(() => {
            count += 1;
          });
          observer.observe(el, { attributes: true, attributeFilter: ["data-trace"] });
          setTimeout(() => {
            observer.disconnect();
            resolve(count);
          }, 1200);
        }),
    );
    expect(flips, "no further state changes after done").toBe(0);
    // The manual activations above consumed the autoplay: entering the start marker now does not replay.
    await page.evaluate(() => {
      const start = document.querySelector("[data-trace-start]")!;
      window.scrollTo(0, start.getBoundingClientRect().top + window.scrollY - 200);
    });
    await page.waitForTimeout(800);
    await expect(trace).toHaveAttribute("data-trace", "done");
    expect(errors).toEqual([]);
  });

  test("reduced motion: no autoplay, complete path, and a truthful highlight toggle on both figures", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const trace = page.locator("[data-trace]");
    await trace.scrollIntoViewIfNeeded();
    await page.waitForTimeout(900);
    await expect(trace).toHaveAttribute("data-trace", "idle");
    const base = await trace.locator(".trace-edge svg > path:first-child").evaluateAll((els) => els.map((el) => getComputedStyle(el).opacity));
    expect(base).toEqual(["1", "1", "1", "1"]);
    const button = page.locator("[data-trace-button]");
    await expect(button).toHaveText("Highlight the path");
    await button.click();
    await expect(trace).toHaveAttribute("data-trace", "emphasis");
    await expect(button).toHaveText("Clear highlight");
    await expect(button).toHaveAttribute("aria-pressed", "true");
    const lit = await trace.locator(".trace-node").evaluateAll((els) => els.map((el) => getComputedStyle(el).borderColor));
    expect(lit.every((c) => c === "rgb(166, 61, 34)")).toBe(true);
    await expect(page.locator("[data-trace-status]")).toHaveText("Path highlighted");
    await button.click();
    await expect(trace).toHaveAttribute("data-trace", "done");
    await expect(button).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator("[data-trace-status]")).toHaveText("Highlight cleared");
    await expect(page.getByRole("group", { name: "Convoy runtime boundary" })).toBeVisible();
    // Hero: no autoplay, same toggle.
    await page.evaluate(() => window.scrollTo(0, 0));
    const hero = page.locator("[data-trace-hero]");
    await hero.scrollIntoViewIfNeeded();
    await page.waitForTimeout(600);
    await expect(hero).toHaveAttribute("data-trace-hero", "idle");
    const replay = page.locator("[data-hero-replay]");
    await expect(replay).toHaveText("Highlight the release");
    await replay.click();
    await expect(hero).toHaveAttribute("data-trace-hero", "emphasis");
    expect(await page.locator("[data-envelope]").evaluate((el) => getComputedStyle(el).borderStyle)).toBe("solid");
    await replay.click();
    await expect(hero).toHaveAttribute("data-trace-hero", "done");
  });

  test("execution trace is visibly drawn: terracotta stroke and node fill at the midpoint, immediate control feedback, release at the end", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    const trace = page.locator("[data-trace]");
    const button = page.locator("[data-trace-button]");
    const status = page.locator("[data-trace-status]");
    const progress = page.locator("[data-trace-progress]");
    await trace.scrollIntoViewIfNeeded();
    await expect(trace).toHaveAttribute("data-trace", "done", { timeout: 6000 });
    await page.screenshot({ path: "tests/screenshots/trace-1440-before.png" });
    await button.click();
    await expect(button).toHaveText("Tracing…");
    await expect(status).toHaveText("Tracing the path");
    await expect(progress).toContainText("Tracing");
    // The visible progress names successive nodes; the live announcement does not change between them.
    await page.waitForTimeout(300);
    const earlyProgress = await progress.textContent();
    const earlyLive = await status.textContent();
    await page.waitForTimeout(800);
    await page.screenshot({ path: "tests/screenshots/trace-1440-mid.png" });
    const mid = await trace.evaluate((root) => {
      const nodes = Array.from(root.querySelectorAll<HTMLElement>(".trace-node")).map((el) => ({
        border: getComputedStyle(el).borderColor,
        background: getComputedStyle(el).backgroundColor,
      }));
      const overlays = Array.from(root.querySelectorAll<SVGPathElement>(".trace-overlay")).map((el) => ({
        opacity: getComputedStyle(el).opacity,
        offset: parseFloat(getComputedStyle(el).strokeDashoffset),
      }));
      return { nodes, overlays };
    });
    const litNodes = mid.nodes.filter((n) => n.border === "rgb(166, 61, 34)" && n.background === "rgb(249, 233, 225)");
    expect(litNodes.length, "nodes already reached are filled and outlined in terracotta").toBeGreaterThanOrEqual(2);
    expect(mid.nodes.filter((n) => n.border !== "rgb(166, 61, 34)").length, "nodes not yet reached are still at base").toBeGreaterThanOrEqual(1);
    const drawn = mid.overlays.filter((o) => o.opacity === "1" && o.offset < 0.5);
    expect(drawn.length, "at least one connector is drawn in terracotta at the midpoint").toBeGreaterThanOrEqual(1);
    await expect(progress).toContainText(/Input processing|Model|Action processing/);
    expect(await progress.textContent()).not.toBe(earlyProgress);
    expect(await status.textContent(), "intermediate nodes are not announced").toBe(earlyLive);
    expect(await status.evaluate((el) => el.getAttribute("aria-live"))).toBe("polite");
    expect(await progress.evaluate((el) => el.getAttribute("aria-live"))).toBeNull();
    await expect(trace).toHaveAttribute("data-trace", "done", { timeout: 4000 });
    await expect(button).toHaveText("Trace again");
    await expect(status).toHaveText("Trace complete");
    await expect(progress).toHaveText("Trace complete");
    await page.waitForTimeout(600);
    await page.screenshot({ path: "tests/screenshots/trace-1440-after.png" });
    const after = await trace.locator(".trace-node").evaluateAll((els) => els.map((el) => getComputedStyle(el).borderColor));
    expect(after.every((c) => c !== "rgb(166, 61, 34)"), "nodes release after the run").toBe(true);
    const overlaysAfter = await trace.locator(".trace-overlay").evaluateAll((els) => els.map((el) => getComputedStyle(el).opacity));
    expect(overlaysAfter.every((o) => o === "0")).toBe(true);
  });

  test("on a phone the trace control sits at the top of the figure and a tap plays nodes that are on screen", async ({ browser }) => {
    const context = await browser.newContext({ viewport: { width: 393, height: 700 }, hasTouch: true });
    const page = await context.newPage();
    await page.goto("/");
    const button = page.locator("[data-trace-button]");
    const controls = page.locator("[data-trace-controls]");
    const firstNode = page.locator("[data-node]").first();
    // Scroll the control to the top of the viewport, under the sticky header, as a reader would.
    await controls.evaluate((el) => el.scrollIntoView({ block: "start", behavior: "instant" }));
    const before = await page.evaluate(() => window.scrollY);
    expect((await controls.boundingBox())!.y).toBeLessThan((await firstNode.boundingBox())!.y);
    await page.touchscreen.tap((await button.boundingBox())!.x + 20, (await button.boundingBox())!.y + 20);
    await expect(page.locator("[data-trace]")).toHaveAttribute("data-trace", "playing");
    expect(await page.evaluate(() => window.scrollY), "no scroll hijack").toBe(before);
    const visible = await page.locator("[data-node] .trace-node").evaluateAll((els) =>
      els.filter((el) => {
        const r = el.getBoundingClientRect();
        return r.top >= 0 && r.bottom <= window.innerHeight;
      }).length,
    );
    expect(visible, "several animated nodes are inside the viewport when the run starts").toBeGreaterThanOrEqual(3);
    await page.waitForTimeout(700);
    await page.screenshot({ path: "tests/screenshots/trace-393-mid.png" });
    await expect(page.locator("[data-trace-progress]")).toContainText("Tracing");
    await expect(page.locator("[data-trace]")).toHaveAttribute("data-trace", "done", { timeout: 4000 });
    await noHorizontalOverflow(page);
    await context.close();
  });

  test("hero sequence: complete at first paint, plays once when the figure enters view, no layout shift, replay by pointer and keyboard", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    // Phone: the figure starts below the first screen, so nothing plays until it is reached.
    await page.setViewportSize({ width: 393, height: 700 });
    await page.goto("/");
    const hero = page.locator("[data-trace-hero]");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForTimeout(500);
    await expect(hero).toHaveAttribute("data-trace-hero", "idle");
    const heightBefore = await hero.evaluate((el) => el.getBoundingClientRect().height);
    await hero.scrollIntoViewIfNeeded();
    await expect(hero).toHaveAttribute("data-trace-hero", "playing", { timeout: 3000 });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: "tests/screenshots/hero-393-mid.png" });
    const mid = await hero.evaluate((root) => ({
      model: getComputedStyle(root.querySelector("[data-endpoint='model']")!).backgroundColor,
      parts: Array.from(root.querySelectorAll(".hero-part")).map((el) => getComputedStyle(el).backgroundColor),
      height: root.getBoundingClientRect().height,
    }));
    expect(mid.model).toBe("rgb(249, 233, 225)");
    expect(mid.parts.filter((c) => c === "rgb(249, 233, 225)").length, "some release parts have taken their accent").toBeGreaterThanOrEqual(2);
    expect(mid.parts.filter((c) => c !== "rgb(249, 233, 225)").length, "and some have not yet").toBeGreaterThanOrEqual(1);
    expect(Math.abs(mid.height - heightBefore), "no layout shift while playing").toBeLessThan(1);
    await page.waitForTimeout(1300);
    const late = await page.locator("[data-envelope]").evaluate((el) => ({ style: getComputedStyle(el).borderStyle, color: getComputedStyle(el).borderColor }));
    expect(late.style).toBe("solid");
    expect(late.color).toBe("rgb(166, 61, 34)");
    await expect(hero).toHaveAttribute("data-trace-hero", "done", { timeout: 3000 });
    await page.waitForTimeout(600);
    expect(await page.locator("[data-envelope]").evaluate((el) => getComputedStyle(el).borderStyle)).toBe("dashed");
    expect(Math.abs((await hero.evaluate((el) => el.getBoundingClientRect().height)) - heightBefore)).toBeLessThan(1);
    // Replay control: pointer, then keyboard; label and status give immediate feedback.
    const replay = page.locator("[data-hero-replay]");
    expect((await replay.boundingBox())!.height).toBeGreaterThanOrEqual(48);
    await replay.click();
    await expect(replay).toHaveText("Playing…");
    await expect(page.locator("[data-hero-status]")).toContainText("Model");
    await expect(hero).toHaveAttribute("data-trace-hero", "done", { timeout: 4000 });
    await replay.focus();
    await page.keyboard.press("Enter");
    await expect(hero).toHaveAttribute("data-trace-hero", "playing");
    await page.keyboard.press("Space");
    await expect(hero).toHaveAttribute("data-trace-hero", "playing");
    await expect(hero).toHaveAttribute("data-trace-hero", "done", { timeout: 4000 });
    // Desktop: the figure is in view at load, so it plays once on its own.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/");
    await expect(page.locator("[data-trace-hero]")).toHaveAttribute("data-trace-hero", "playing", { timeout: 3000 });
    await page.waitForTimeout(1200);
    await page.screenshot({ path: "tests/screenshots/hero-1440-mid.png" });
    await expect(page.locator("[data-trace-hero]")).toHaveAttribute("data-trace-hero", "done", { timeout: 4000 });
    expect(errors).toEqual([]);
  });

  test("WCAG text-spacing overrides and 200% text enlargement do not clip, truncate, or overflow", async ({ page }) => {
    const clipped = () =>
      page.evaluate(() => {
        const bad: string[] = [];
        for (const el of Array.from(document.querySelectorAll<HTMLElement>("h1, h2, h3, p, li, a, button, summary, dt, dd, span"))) {
          if (el.closest(".sr-only") || el.closest("details:not([open]) > :not(summary)")) continue;
          const cs = getComputedStyle(el);
          if (cs.display === "none") continue;
          // Visually hidden text (the sr-only pattern: 1px box, absolute, clipped) is not a clipping defect.
          if (cs.position === "absolute" && el.clientWidth <= 1 && el.clientHeight <= 1) continue;
          if ((cs.overflow === "hidden" || cs.overflowX === "hidden") && el.scrollWidth > el.clientWidth + 1) bad.push(el.tagName + ":" + (el.textContent || "").trim().slice(0, 30));
        }
        return bad;
      });
    for (const width of [1440, 393]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/");
      // WCAG 1.4.12 text spacing, applied as a user stylesheet would.
      await page.addStyleTag({
        content: "* { line-height: 1.5 !important; letter-spacing: 0.12em !important; word-spacing: 0.16em !important; } p { margin-bottom: 2em !important; }",
      });
      await page.locator("details:not([open]) > summary").evaluateAll((summaries) => summaries.forEach((s) => ((s.parentElement as HTMLDetailsElement).open = true)));
      await noHorizontalOverflow(page);
      expect(await clipped(), `text-spacing clips nothing at ${width}`).toEqual([]);
      // 200% text enlargement: the type scale is in rem, so doubling the root size doubles the text.
      await page.goto("/");
      await page.evaluate(() => (document.documentElement.style.fontSize = "200%"));
      await page.locator("details:not([open]) > summary").evaluateAll((summaries) => summaries.forEach((s) => ((s.parentElement as HTMLDetailsElement).open = true)));
      await noHorizontalOverflow(page);
      expect(await clipped(), `200% text clips nothing at ${width}`).toEqual([]);
      const h1 = await page.locator("h1").evaluate((el) => parseFloat(getComputedStyle(el).fontSize));
      expect(h1).toBeGreaterThanOrEqual(80);
      await expect(page.getByRole("group", { name: "Convoy runtime boundary" })).toBeVisible();
      await expect(page.locator("[data-contact-address]")).toBeVisible();
      // The header may be taller now; anchors still land below it, and the menu still closes on Escape.
      const headerHeight = await page.locator("header").evaluate((el) => el.getBoundingClientRect().height);
      const padding = await page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop));
      expect(padding, `scroll padding follows the measured header at ${width}`).toBeGreaterThanOrEqual(headerHeight);
      if (width < 900) {
        await page.getByRole("button", { name: "Menu" }).click();
        const menu = page.locator("[data-mobile-menu]");
        await expect(menu).toBeVisible();
        const fits = await menu.evaluate((el) => el.getBoundingClientRect().bottom <= window.innerHeight + 1 || getComputedStyle(el).overflowY === "auto");
        expect(fits).toBe(true);
        await page.keyboard.press("Escape");
        await expect(page.getByRole("button", { name: "Menu" })).toBeFocused();
        await page.getByRole("button", { name: "Menu" }).click();
        await menu.getByRole("link", { name: "Contact" }).click();
      } else {
        await page.locator("header nav").first().getByRole("link", { name: "Contact" }).click();
      }
      await expect(page).toHaveURL(/#contact$/);
      const top = await page.locator("#contact").evaluate((el) => el.getBoundingClientRect().top);
      expect(top, `contact anchor clears the enlarged header at ${width}`).toBeGreaterThanOrEqual(headerHeight - 1);
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.locator(width < 900 ? "[data-mobile-menu]" : "header nav").first().evaluate(() => {});
      if (width < 900) {
        await page.getByRole("button", { name: "Menu" }).click();
        await page.locator("[data-mobile-menu]").getByRole("link", { name: "How it works" }).click();
      } else {
        await page.locator("header nav").first().getByRole("link", { name: "How it works" }).click();
      }
      const workflowTop = await page.locator("#workflow").evaluate((el) => el.getBoundingClientRect().top);
      expect(workflowTop, `workflow anchor clears the enlarged header at ${width}`).toBeGreaterThanOrEqual(headerHeight - 1);
    }
  });

  for (const viewport of VIEWPORTS) {
    test(`no accessibility violations and no sideways scroll at ${viewport.name}`, async ({ page }) => {
      await page.setViewportSize({ width: viewport.width, height: viewport.height });
      await page.goto("/");
      await noHorizontalOverflow(page);
      const figure = page.locator("#execution figure");
      expect(await figure.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), "the execution figure does not clip sideways").toBe(true);
      const geometry = await page.getByRole("group", { name: "Convoy runtime boundary" }).evaluate((group) => {
        const g = group.getBoundingClientRect();
        return Array.from(group.querySelectorAll("[data-node]")).map((n) => {
          const r = n.getBoundingClientRect();
          return r.left >= g.left && r.right <= g.right && r.top >= g.top && r.bottom <= g.bottom;
        });
      });
      expect(geometry, "Convoy nodes sit inside the boundary").toEqual([true, true, true]);
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
