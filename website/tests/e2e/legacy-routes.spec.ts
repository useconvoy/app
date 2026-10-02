import { expect, test } from "@playwright/test";
import { h1, mockApi } from "./support/configurations";

/*
 * The retired console: Applications (`/app/applications`, with its Device, Chat, Usage and
 * Traces views) and its entry points `/app/device`, `/portal` and `/console`. Each now leads
 * to Configurations with a permanent redirect; a request's query is passed through.
 */
const RETIRED = ["/app/applications", "/app/device", "/portal", "/console"];

test("the retired console's routes redirect permanently to Configurations", async ({ request }) => {
  for (const path of RETIRED.flatMap(path => [path, `${path}/`])) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status(), path).toBe(308);
    expect(response.headers()["location"], path).toBe("/app/configurations");
  }
  const deepLinks: Array<[string, string]> = [
    ["/app/applications?section=device&view=device", "/app/configurations?section=device&view=device"],
    ["/app/device?view=chat", "/app/configurations?view=chat"],
    ["/portal/?view=usage", "/app/configurations?view=usage"],
  ];
  for (const [path, location] of deepLinks) {
    const response = await request.get(path, { maxRedirects: 0 });
    expect(response.status(), path).toBe(308);
    expect(response.headers()["location"], path).toBe(location);
  }
  // Nothing below the retired routes is served, and the other redirects are unchanged.
  for (const path of ["/app/applications/device", "/console/applications"]) {
    expect((await request.get(path, { maxRedirects: 0 })).status(), path).toBe(404);
  }
  const marketing = await request.get("/platform", { maxRedirects: 0 });
  expect(marketing.status()).toBe(308);
  expect(marketing.headers()["location"]).toBe("/");
  const entry = await request.get("/app", { maxRedirects: 0 });
  expect(entry.status()).toBe(307);
  expect(entry.headers()["location"]).toBe("/app/projects");
});

test("an old device link opens the Configurations sign-in, which stays out of search indexing", async ({ page, request }) => {
  const html = await (await request.get("/portal")).text();
  expect(html).toContain('name="robots" content="noindex, nofollow"');
  expect(html).toContain('name="googlebot" content="noindex, nofollow"');
  await mockApi(page, { signedIn: false });
  await page.goto("/app/device?view=chat");
  await expect(page).toHaveURL(/\/app\/configurations\/?\?view=chat$/);
  await expect(page.getByRole("heading", { name: "One workspace for your robots." })).toBeVisible();
  await page.getByLabel("Email", { exact: true }).fill("fixture@example.test");
  await page.getByLabel("Password", { exact: true }).fill("fixture-only");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(h1(page)).toHaveText("Configurations");
  await expect(page.getByRole("heading", { name: /Your device, observed|Talk to your model/ })).toHaveCount(0);
  await expect(page.locator('a[href*="/app/applications"], a[href*="/app/device"], a[href^="/portal"], a[href^="/console"]')).toHaveCount(0);
});
