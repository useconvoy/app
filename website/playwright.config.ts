import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against the app served on :3100 with the runtime's local stack
 * (`make e2e-up` + control plane on :8700) behind it — no mocked domain.
 * tests/e2e/global-setup.ts provisions the website database and a signed-in
 * storage state.
 */
export default defineConfig({
  testDir: "./tests/e2e",
  globalSetup: "./tests/e2e/global-setup.ts",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.WEBSITE_BASE_URL ?? "http://localhost:3100",
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev -- --port 3100",
    url: "http://localhost:3100",
    reuseExistingServer: true,
    timeout: 120_000,
    env: {
      WEBSITE_PG_DSN:
        process.env.WEBSITE_PG_DSN ??
        "postgresql://convoy_website_app:convoy_website_app@localhost:5433/convoy_website",
      WEBSITE_PG_ADMIN_DSN:
        process.env.WEBSITE_PG_ADMIN_DSN ??
        "postgresql://convoy_admin:convoy_admin@localhost:5433/convoy_website",
      SESSION_SECRET: process.env.SESSION_SECRET ?? "e2e-session-secret-e2e-session-secret",
      // The waitlist gate applies to every sign-in; the suite's throwaway
      // identities live under example.com.
      CONVOY_ALLOWED_EMAILS: process.env.CONVOY_ALLOWED_EMAILS ?? "@example.com",
      CONVOY_CONTROL_PLANE_URL: process.env.CONVOY_CONTROL_PLANE_URL ?? "http://localhost:8700",
      CONVOY_CONTROL_PLANE_TOKEN: process.env.CONVOY_CONTROL_PLANE_TOKEN ?? "e2e-dev-token",
    },
  },
});
