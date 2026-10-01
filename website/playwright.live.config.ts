import { defineConfig, devices } from "@playwright/test";

/**
 * Live journeys against a deployed Convoy website (tests/live, `pnpm run test:live`).
 *
 * They run only when CONVOY_LIVE_BASE_URL, CONVOY_LIVE_EMAIL and
 * CONVOY_LIVE_PASSWORD are set in the environment; otherwise every test is
 * skipped. Credentials are read from the environment only, sent only to the
 * site's own sign-in route, and never printed. The read-only journeys change
 * nothing; the write journeys run only with CONVOY_LIVE_ALLOW_WRITES=1 and put
 * the account's workspace document back as they found it, whatever happens.
 *
 * Videos and milestone screenshots (the account email masked) go to
 * CONVOY_LIVE_OUTPUT_DIR (default test-results/live). Traces are off: a trace
 * would record request bodies, including the sign-in.
 */
const outputDir = process.env.CONVOY_LIVE_OUTPUT_DIR || "test-results/live";

export default defineConfig({
  testDir: "./tests/live",
  outputDir,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  workers: 1,
  retries: 0,
  timeout: 180_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  use: {
    baseURL: process.env.CONVOY_LIVE_BASE_URL,
    trace: "off",
    screenshot: "off",
    video: { mode: "on", size: { width: 1280, height: 800 } },
    actionTimeout: 20_000,
    navigationTimeout: 45_000,
  },
  projects: [
    {
      name: "live-chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1280, height: 800 },
        launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH } : {},
      },
    },
  ],
});
