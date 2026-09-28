/** One browser acceptance against a running, authenticated API/worker/simulator stack.
 * Usage: node scripts/test-live-console.mjs CONNECTION_JSON NEW_OUTPUT_DIRECTORY
 * Create the stack with examples/manipulation/pipeline.py --serve first.
 */
import { chromium, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const website = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [connectionPath, outputPath] = process.argv.slice(2);
if (!connectionPath || !outputPath) throw new Error('Pass connection.json and a new output directory');
const connection = JSON.parse(await fs.readFile(connectionPath, 'utf8'));
if (!['127.0.0.1', 'localhost', '[::1]'].includes(new URL(connection.api_url).hostname)) {
  throw new Error('The acceptance harness must use a local API');
}
const output = path.resolve(outputPath);
await fs.mkdir(output, { recursive: false });
const reserved = net.createServer();
await new Promise(resolve => reserved.listen(0, '127.0.0.1', resolve));
const port = reserved.address().port;
await new Promise(resolve => reserved.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(port)], {
  cwd: website,
  env: { ...process.env, CONVOY_API_URL: connection.api_url, CONVOY_CONSOLE_ORIGIN: origin },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let serverOutput = '';
for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => {
  serverOutput = (serverOutput + chunk.toString()).slice(-16384);
});
let browser;
const report = { status: 'failed', checks: [] };
try {
  await expect.poll(async () => {
    if (server.exitCode !== null) throw new Error('Console server exited before readiness');
    return fetch(origin + '/console').then(response => response.status).catch(() => 0);
  }, { timeout: 30000 }).toBe(200);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1120 } });
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.goto(origin + '/console');
  await page.getByLabel('Email', { exact: true }).fill(connection.email);
  await page.getByLabel('Password', { exact: true }).fill(connection.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Robots', exact: true })).toBeVisible();
  const deploy = page.getByRole('button', { name: 'Request deployment', exact: true });
  await expect(deploy).toBeEnabled();
  const before = await page.locator('[aria-labelledby="deployment-heading"]').innerText();
  const generation = Number(before.match(/Generation (\d+)/)?.[1]);
  if (!generation) throw new Error('Seeded deployment not observed');
  await deploy.click();
  await expect(page.getByText(`Generation ${generation + 1}`, { exact: true })).toBeVisible({ timeout: 15000 });
  const start = page.getByRole('button', { name: 'Start mission', exact: true });
  await expect(start).toBeEnabled({ timeout: 15000 });
  const rows = page.locator('.console-history tbody tr');
  const previous = await rows.count() ? await rows.first().locator('code').innerText() : null;
  await start.click();
  if (previous) await expect(rows.first()).not.toContainText(previous);
  await expect(rows.first()).toContainText('completed', { timeout: 20000 });
  await page.getByRole('button', { name: 'View episode', exact: true }).first().click();
  await expect(page.locator('.console-episode')).toContainText('Succeeded');
  await expect(page.locator('.console-episode')).toContainText('500');
  await page.screenshot({ path: path.join(output, 'console.png'), fullPage: true });
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Robots', exact: true })).toBeVisible();
  await expect(start).toBeEnabled();
  await start.click();
  await page.getByRole('button', { name: 'Request cancellation', exact: true }).click({ timeout: 5000 });
  await expect(rows.first()).toContainText('cancelled', { timeout: 15000 });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  await page.screenshot({ path: path.join(output, 'console-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  expect(failures).toEqual([]);
  report.status = 'passed';
  report.checks = ['user authentication', 'new deployment generation', 'explicit mission start', '500 real physics steps',
    'benchmark success separate from completion', 'session survives reload', 'acknowledged cancellation',
    'mobile without overflow', 'logout'];
  console.log(JSON.stringify(report));
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
  const force = setTimeout(() => server.kill('SIGKILL'), 5000);
  if (server.exitCode === null) await new Promise(resolve => server.once('exit', resolve));
  clearTimeout(force);
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(report, null, 2));
  await fs.writeFile(path.join(output, 'web.log'), serverOutput);
}
