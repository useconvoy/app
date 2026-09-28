/** One paired evaluation against an already-running API, jobs, planner, policy
 * worker and simulator. Starts/stops only this check's web server and browser.
 * Usage: node scripts/test-live-pairing.mjs CONNECTION_JSON NEW_OUTPUT_DIRECTORY
 */
import { chromium, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const website = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [connectionPath, outputPath] = process.argv.slice(2);
if (!connectionPath || !outputPath) throw new Error('Pass private connection.json and a new output directory');
const connection = JSON.parse(await fs.readFile(connectionPath, 'utf8'));
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(connection.api_url).hostname)) throw new Error('Use a local acceptance API');
if (connection.planner_backend_kind !== 'controlled-text-planner') throw new Error('This qualification expects an explicitly labeled controlled text planner');
const manifest = JSON.parse(await fs.readFile(connection.manifest_path, 'utf8'));
if (manifest.schema_version !== 2 || manifest.profile !== 'metaworld-smolvla-text-skill-v1') throw new Error('A paired release is required');
if (manifest.planner.runtime !== 'convoy-controlled-text-skill-v1' || manifest.placement.planner !== 'development-local-controlled') {
  throw new Error('Controlled qualification must declare its own runtime and local placement');
}
const output = path.resolve(outputPath);
await fs.mkdir(output, { recursive: false });
const reserved = net.createServer();
await new Promise(resolve => reserved.listen(0, '127.0.0.1', resolve));
const port = reserved.address().port;
await new Promise(resolve => reserved.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const server = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(port)], {
  cwd: website, env: { ...process.env, CONVOY_API_URL: connection.api_url, CONVOY_CONSOLE_ORIGIN: origin },
  stdio: ['ignore', 'pipe', 'pipe'],
});
let logs = '';
for (const stream of [server.stdout, server.stderr]) stream.on('data', chunk => { logs = (logs + chunk.toString()).slice(-16000); });
let browser;
const evidence = { status: 'failed', planner_backend_kind: connection.planner_backend_kind,
  scope: 'One fixed seed with a controlled text planner and pretrained SmolVLA/MuJoCo action policy. No Jetson/cloud planner model or physical-robot qualification.', checks: [] };
try {
  await expect.poll(async () => {
    if (server.exitCode !== null) throw new Error('Console exited before readiness');
    return fetch(origin + '/console').then(response => response.status).catch(() => 0);
  }, { timeout: 30000 }).toBe(200);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin + '/console');
  await page.getByLabel('Email', { exact: true }).fill(connection.email);
  await page.getByLabel('Password', { exact: true }).fill(connection.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await page.getByRole('combobox', { name: 'Project', exact: true }).selectOption(connection.project_id);
  await page.getByRole('combobox', { name: 'Selected robot', exact: true }).selectOption(connection.robot_id);
  await page.getByRole('combobox', { name: 'Release', exact: true }).selectOption(connection.release_id);
  const components = page.locator('.console-release-details');
  await expect(components).toContainText(manifest.profile);
  await expect(components).toContainText(manifest.action_manifest.policy.runtime);
  await expect(components).toContainText(manifest.planner.runtime);
  await expect(components).toContainText('Configured action placement: local development CPU');
  await expect(components).toContainText('Configured planner placement: local controlled planner fixture');
  await expect(components).toContainText('no text-model inference');
  await components.getByText('Pinned component identities', { exact: true }).click();
  for (const digest of [manifest.action_manifest.policy.artifact_sha256, manifest.planner.artifact_sha256, manifest.planner.protocol_sha256]) {
    await expect(components).toContainText(digest);
  }
  const connect = page.locator('summary', { hasText: 'Connect a simulator' });
  await connect.click();
  await expect(page.getByRole('combobox', { name: 'Simulator profile', exact: true })
    .locator(`option[value="${manifest.profile}"]`)).toHaveJSProperty('disabled', false);
  await connect.click();

  const panel = page.locator('[aria-labelledby="evaluations-heading"]');
  await panel.locator('summary', { hasText: 'Create an immutable suite' }).click();
  await page.getByLabel('Suite name', { exact: true }).fill('Paired browser qualification');
  await page.getByLabel('Scenario seeds', { exact: true }).fill('0');
  await page.getByLabel('Required successes', { exact: true }).fill('1');
  await page.getByRole('button', { name: 'Create suite', exact: true }).click();
  const evaluate = page.getByRole('button', { name: 'Evaluate selected release', exact: true });
  await expect(evaluate).toBeEnabled(); // the suite matches the outer paired profile
  await evaluate.click();
  await expect(panel.locator('.console-reservation')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Request deployment', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: 'Start mission', exact: true })).toBeDisabled();
  await expect(panel.locator('.console-evaluation-outcome')).toContainText('Passed', { timeout: 330000 });
  const evaluationId = await page.getByRole('combobox', { name: 'Evaluation run', exact: true }).inputValue();
  const result = await page.evaluate(async id => (await fetch(`/api/platform/evaluations/${id}`)).json(), evaluationId);
  expect(result.report.passed).toBe(true);
  expect(result.report.cases).toHaveLength(1);
  const recorded = result.report.cases[0];
  expect(recorded.evidence_valid).toBe(true);
  expect(recorded.steps).toBeGreaterThan(0);
  expect(recorded.steps).toBeLessThanOrEqual(manifest.action_manifest.execution.max_steps);
  const episode = await page.evaluate(async id => (await fetch(`/api/platform/episodes/${id}`)).json(), recorded.episode_id);
  expect(episode.summary.planner_accepted).toBe(true);
  expect(episode.summary.planner_result.decision).toEqual({ kind: 'skill', skill_id: manifest.task.skill_id, parameters: {} });
  expect(episode.summary.planner_result.planner_artifact_sha256).toBe(manifest.planner.artifact_sha256);
  expect(episode.summary.policy_runtime).toBe(manifest.action_manifest.policy.runtime);
  expect(episode.summary.final_success).toBe(true);
  expect(episode.summary.planner_result.identity.release_digest).toBe(episode.release_digest);
  await page.getByRole('button', { name: 'View case episode', exact: true }).click();
  await expect(page.locator('.console-episode')).toContainText('Succeeded');
  await expect(page.locator('.console-episode')).toContainText('planner_accepted');
  await page.getByRole('button', { name: 'Promote passing report', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Report promoted', exact: true })).toBeDisabled();
  await page.screenshot({ path: path.join(output, 'paired-console.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  await page.screenshot({ path: path.join(output, 'paired-console-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
  Object.assign(evidence, { status: 'passed', manifest, evaluation: result, episode,
    checks: ['paired manifest rendered without a root policy field', 'declared component identities and placement',
      'registration capability enabled by API', 'suite uses paired execution profile', 'reservation blocks ordinary work',
      'accepted controlled planner result and real learned-policy episode', 'explicit promotion', 'mobile without overflow', 'logout'] });
  console.log(JSON.stringify({ status: evidence.status, planner_backend_kind: evidence.planner_backend_kind, steps: recorded.steps, checks: evidence.checks }));
} finally {
  if (browser) await browser.close();
  server.kill('SIGTERM');
  const force = setTimeout(() => server.kill('SIGKILL'), 5000);
  if (server.exitCode === null) await new Promise(resolve => server.once('exit', resolve));
  clearTimeout(force);
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(evidence, null, 2));
  await fs.writeFile(path.join(output, 'web.log'), logs);
}
