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
    // One release envelope, below the hero, with all six parts as rows and the examples behind one disclosure.
    const envelope = page.locator("[data-envelope]");
    await expect(envelope).toHaveCount(1);
    await expect(envelope).toContainText("Robot-policy release");
    await expect(envelope.locator("[data-field]")).toHaveCount(6);
    for (const part of ["Release identity", "Model assets", "Input / action processing", "Runtime", "Target configuration", "Evaluation evidence"]) {
      await expect(envelope.getByText(part, { exact: true })).toBeVisible();
    }
    const details = page.locator("#problem details");
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
    await expect(page.locator("#problem figure button, #execution figure button")).toHaveCount(0);
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
    const hero = page.locator("#problem figure");
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

  test("both diagrams are static and complete at first paint, in normal and reduced motion, on desktop and phones", async ({ page }) => {
    const sizes = [
      { name: "desktop-1440", width: 1440, height: 900 },
      { name: "iphone-pro-393", width: 393, height: 852 },
      { name: "iphone-pro-max-430", width: 430, height: 932 },
    ];
    for (const reducedMotion of ["no-preference", "reduce"] as const) {
      await page.emulateMedia({ reducedMotion });
      for (const size of sizes) {
        const label = `${size.name} (${reducedMotion})`;
        await page.setViewportSize({ width: size.width, height: size.height });
        await page.goto("/");
        const hero = page.locator("#problem figure");
        const execution = page.locator("#execution figure");
        // No control, live region, progress line or sequence state inside either figure.
        for (const figure of [hero, execution]) {
          await expect(figure.locator("button, [role='status'], [aria-live], [data-trace], [data-trace-hero], [data-trace-button], [data-hero-replay], [data-trace-progress]"), `no motion remnants in ${label}`).toHaveCount(0);
          await expect(figure.getByText(/replay|playing|trace the path|tracing|trace again|highlight/i)).toHaveCount(0);
          // Nothing in the diagram runs an animation, and every visible element is fully opaque at first paint.
          // The decorative etched field behind the hero diagram is covered by pixel-field.spec.ts.
          const moving = await figure.evaluate((root) =>
            Array.from(root.querySelectorAll("*"))
              .filter((el) => !el.closest(".etched-waves") && !el.closest(".etched-grid"))
              .filter((el) => {
                const cs = getComputedStyle(el);
                return cs.animationName !== "none" || (cs.display !== "none" && parseFloat(cs.opacity) < 1);
              })
              .map((el) => el.tagName + "." + el.className),
          );
          expect(moving, `nothing animates or is hidden in ${label}`).toEqual([]);
        }
        // Every node label, note and placement is readable now; the boundary holds its three nodes.
        const nodes = execution.locator("[data-node]");
        await expect(nodes).toHaveCount(5);
        for (const text of ["Sensors", "Input processing", "Model", "Action processing", "Robot controller"]) {
          await expect(execution.getByText(text, { exact: true }).first(), `${text} visible in ${label}`).toBeVisible();
        }
        const boundary = page.getByRole("group", { name: "Convoy runtime boundary" });
        await expect(boundary).toBeVisible();
        await expect(boundary.locator("[data-node]")).toHaveCount(3);
        await expect(execution.getByText("new observations / robot state")).toBeVisible();
        // Hero: model, six release parts, controller, all visible, in order.
        for (const text of ["Trained model", "your team", "Robot controller", "outside Convoy"]) {
          await expect(hero.getByText(text, { exact: true }), `${text} visible in ${label}`).toBeVisible();
        }
        await expect(hero.locator("[data-field]")).toHaveCount(6);
        for (const part of await hero.locator("[data-field]").all()) await expect(part).toBeVisible();
        const order = await hero.locator("[data-endpoint], [data-envelope]").evaluateAll((els) => els.map((el) => el.getBoundingClientRect().top));
        expect(order[0]).toBeLessThan(order[1]);
        expect(order[1]).toBeLessThan(order[2]);
        // Nothing moves after paint: the same geometry a moment later.
        const before = await execution.evaluate((el) => JSON.stringify(el.getBoundingClientRect()));
        await page.waitForTimeout(700);
        expect(await execution.evaluate((el) => JSON.stringify(el.getBoundingClientRect())), `no layout movement in ${label}`).toBe(before);
        await noHorizontalOverflow(page);
        // The page's real interactions still work: the details disclosure below the hero and the contact link.
        const details = page.locator("#problem details");
        await details.locator("summary").click();
        await expect(page.locator("[data-release-details]")).toBeVisible();
        await details.locator("summary").click();
        await expect(page.locator("[data-release-details]")).toBeHidden();
        await expect(page.locator("[data-contact-link]")).toHaveAttribute("href", /^mailto:founders@deployconvoy\.com\?subject=/);
        if (reducedMotion === "no-preference") await page.screenshot({ path: `tests/screenshots/static-${size.name}.png`, fullPage: true });
      }
    }
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
