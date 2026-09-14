import { defineConfig, devices } from "@playwright/test";

/**
 * The suite runs against the production build (`next build` first, then
 * `next start`), so what it checks is what the container would serve.
 */
const PORT = process.env.PORT ?? "3100";
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        // The session image ships one Chromium at a fixed path; a pinned
        // @playwright/test release would otherwise try to download its own.
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
          ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
          : {},
      },
    },
    {
      name: "webkit",
      // The responsive layout, navigation and accessibility suite also runs
      // in Safari's engine. The CDP touch gesture test stays Chromium-only.
      testMatch: "**/landing.spec.ts",
      use: { ...devices["Desktop Safari"] },
    },
    ...([
      ["iphone", "iPhone 13"],
      ["android", "Pixel 7"],
    ] as const).map(([name, device]) => ({
      name,
      testMatch: "**/landing.spec.ts",
      grep: /renders the approved hero|section order|mobile menu exposes|contact block opens/,
      use: { ...devices[device] },
    })),
  ],
  webServer: {
    command: `pnpm exec next start -p ${PORT}`,
    url: baseURL,
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
