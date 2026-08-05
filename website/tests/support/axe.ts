import axe from "axe-core";
import { expect } from "vitest";

/**
 * Run axe on a rendered container and assert no violations. Rules that
 * need a layout engine or a full page document are disabled: jsdom cannot
 * compute contrast, and components render outside page landmarks here.
 */
export async function expectNoAxeViolations(container: Element): Promise<void> {
  const results = await axe.run(container, {
    rules: {
      "color-contrast": { enabled: false },
      region: { enabled: false },
      "page-has-heading-one": { enabled: false },
      "landmark-one-main": { enabled: false },
    },
  });
  expect(results.violations).toEqual([]);
}
