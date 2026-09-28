/** Real browser → BFF → API → evaluation job → coordinator → MuJoCo acceptance.
 * Start pipeline.py --serve first. CONVOY_EVALUATION_PYTHON must point at the
 * simulation managed environment. This test starts/stops only its own job/web.
 */
import { chromium, expect } from '@playwright/test';
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import fs from 'node:fs/promises';
import net from 'node:net';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const website = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const [connectionPath, outputPath] = process.argv.slice(2);
if (!connectionPath || !outputPath || !process.env.CONVOY_EVALUATION_PYTHON) {
  throw new Error('Pass connection.json, a new output directory and CONVOY_EVALUATION_PYTHON');
}
const connection = JSON.parse(await fs.readFile(connectionPath, 'utf8'));
if (!['localhost', '127.0.0.1', '[::1]'].includes(new URL(connection.api_url).hostname)) throw new Error('Use a local acceptance API');
const dataDir = path.join(path.dirname(path.resolve(connectionPath)), 'server');
await fs.access(path.join(dataDir, 'convoy.db')); // specifically the private SQLite --serve harness
const output = path.resolve(outputPath);
await fs.mkdir(output, { recursive: false });
const reserve = net.createServer();
await new Promise(resolve => reserve.listen(0, '127.0.0.1', resolve));
const port = reserve.address().port;
await new Promise(resolve => reserve.close(resolve));
const origin = `http://127.0.0.1:${port}`;
const processes = [];
let processLog = '';
function start(binary, args, env) {
  const child = spawn(binary, args, { cwd: website, env, stdio: ['ignore', 'pipe', 'pipe'] });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', chunk => { processLog = (processLog + chunk.toString()).slice(-20000); });
  processes.push(child);
  return child;
}
const web = start(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', String(port)],
  { ...process.env, CONVOY_API_URL: connection.api_url, CONVOY_CONSOLE_ORIGIN: origin });
let browser;
const evidence = { status: 'failed', checks: [] };
try {
  await expect.poll(async () => {
    if (web.exitCode !== null) throw new Error('Console server exited');
    return fetch(origin + '/console').then(response => response.status).catch(() => 0);
  }, { timeout: 30000 }).toBe(200);
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  const failures = [];
  page.on('pageerror', error => failures.push(error.message));
  await page.goto(origin + '/console');
  await page.getByLabel('Email', { exact: true }).fill(connection.email);
  await page.getByLabel('Password', { exact: true }).fill(connection.password);
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  const panel = page.locator('[aria-labelledby="evaluations-heading"]');
  const deploy = page.getByRole('button', { name: 'Request deployment', exact: true });
  const startMission = page.getByRole('button', { name: 'Start mission', exact: true });
  await expect(panel).toBeVisible();
  await expect(deploy).toBeEnabled();
  await panel.locator('summary', { hasText: 'Create an immutable suite' }).click();
  await page.getByLabel('Suite name', { exact: true }).fill('Browser qualification');
  await page.getByLabel('Scenario seeds', { exact: true }).fill('0, 1');
  await page.getByRole('button', { name: 'Create suite', exact: true }).click();
  await expect(page.getByRole('combobox', { name: 'Evaluation suite', exact: true })).toContainText('Browser qualification');
  const suiteId = await page.getByRole('combobox', { name: 'Evaluation suite', exact: true }).inputValue();
  await expect(deploy).toBeEnabled();
  await expect(startMission).toBeEnabled();

  const evaluate = page.getByRole('button', { name: 'Evaluate selected release', exact: true });
  const selection = page.getByRole('combobox', { name: 'Evaluation run', exact: true });
  await evaluate.click(); // the job is deliberately not running yet; reservation is durable
  await expect(panel.locator('.console-reservation')).toContainText('is reserved by evaluation');
  await expect(deploy).toBeDisabled();
  await expect(startMission).toBeDisabled();
  await page.getByRole('button', { name: 'Request evaluation cancellation', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Evaluation cancellation awaiting acknowledgement', exact: true })).toBeVisible();
  const jobEnv = { ...process.env, CONVOY_DATA_DIR: dataDir, CONVOY_SIMULATOR: '1', CONVOY_SQLITE_WAL: '0', CONVOY_SCHEDULER_INPROCESS: '0' };
  delete jobEnv.DATABASE_URL;
  start(path.resolve(process.env.CONVOY_EVALUATION_PYTHON), ['-m', 'convoy_server.evaluation_worker'], jobEnv);
  await expect(panel.locator('.console-evaluation-outcome')).toContainText('Did not pass', { timeout: 30000 });
  await expect(panel.locator('.console-reservation')).toHaveCount(0, { timeout: 10000 });

  await page.getByRole('button', { name: 'Require selected suite', exact: true }).click();
  await expect(panel.getByText('The selected release needs a passing promotion', { exact: false })).toBeVisible();
  await expect(deploy).toBeDisabled();
  await expect(startMission).toBeDisabled();

  await evaluate.click();
  await expect(panel.locator('.console-evaluation-outcome')).toContainText('Passed', { timeout: 45000 });
  await expect(panel.locator('.console-evaluation-outcome')).toContainText('2/2 successful cases');
  const baselineId = await selection.inputValue();
  await expect(evaluate).toBeEnabled();
  await evaluate.click();
  await expect(selection).not.toHaveValue(baselineId);
  await expect(panel.locator('.console-evaluation-outcome')).toContainText('Passed', { timeout: 45000 });
  const candidateId = await selection.inputValue();
  await page.getByRole('combobox', { name: 'Compare with baseline', exact: true }).selectOption(baselineId);
  await page.getByRole('button', { name: 'Compare reports', exact: true }).click();
  await expect(panel.getByText('Success-count change: 0', { exact: false })).toBeVisible();
  await page.getByRole('button', { name: 'Promote passing report', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Report promoted', exact: true })).toBeDisabled();
  await expect(deploy).toBeEnabled();
  await deploy.click();
  await expect(startMission).toBeEnabled({ timeout: 15000 });
  const rows = page.locator('[aria-labelledby="episodes-heading"] tbody tr');
  const previous = await rows.first().locator('code').innerText();
  await startMission.click();
  await expect(rows.first()).not.toContainText(previous);
  await expect(rows.first()).toContainText('completed', { timeout: 20000 });
  await rows.first().getByRole('button', { name: 'View episode', exact: true }).click();
  await expect(page.locator('.console-episode')).toContainText('Succeeded');

  // A second real immutable release has the same suite contract but different
  // policy bytes. It has no promotion and is never served by this worker.
  const other = JSON.parse(await fs.readFile(connection.manifest_path, 'utf8'));
  other.policy.artifact_sha256 = other.policy.artifact_sha256 === 'e'.repeat(64) ? 'd'.repeat(64) : 'e'.repeat(64);
  await page.locator('summary', { hasText: 'Register a release manifest' }).click();
  await page.getByLabel('Manifest JSON', { exact: true }).fill(JSON.stringify(other));
  await page.getByRole('button', { name: 'Register release', exact: true }).click();
  const releaseSelect = page.getByRole('combobox', { name: 'Release', exact: true });
  await expect(releaseSelect).not.toHaveValue(connection.release_id);
  const unqualifiedId = await releaseSelect.inputValue();
  await expect(panel.getByText('The selected release needs a passing promotion', { exact: false })).toBeVisible();

  let releaseHeld;
  let requestReady;
  const held = new Promise(resolve => { releaseHeld = resolve; });
  const ready = new Promise(resolve => { requestReady = resolve; });
  let holdOnce = true;
  const pattern = '**/api/platform/applications/*/qualification?release_id=*';
  await page.route(pattern, async route => {
    if (holdOnce && new URL(route.request().url()).searchParams.get('release_id') === connection.release_id) {
      holdOnce = false;
      const response = await route.fetch(); // real upstream response; only delivery is delayed
      requestReady();
      await held;
      await route.fulfill({ response });
    } else await route.continue();
  });
  await releaseSelect.selectOption(connection.release_id);
  await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(new Error('Qualification race was not reached')), 5000))]);
  await releaseSelect.selectOption(unqualifiedId);
  await expect(panel.getByText('The selected release needs a passing promotion', { exact: false })).toBeVisible();
  await expect(deploy).toBeDisabled();
  const delivered = page.waitForResponse(response => response.url().includes(`qualification?release_id=${connection.release_id}`));
  releaseHeld();
  await (await delivered).finished();
  await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await expect(deploy).toBeDisabled();
  await expect(panel.getByText('The selected release needs a passing promotion', { exact: false })).toBeVisible();
  await page.unroute(pattern);
  await releaseSelect.selectOption(connection.release_id);
  await expect(deploy).toBeEnabled();
  await page.reload();
  await page.getByRole('combobox', { name: 'Release', exact: true }).selectOption(connection.release_id);
  await expect(page.getByRole('button', { name: 'Report promoted', exact: true })).toBeDisabled();

  const runs = await page.evaluate(async project => (await fetch(`/api/platform/evaluations?project_id=${project}`)).json(), connection.project_id);
  const suiteRuns = runs.filter(run => run.suite_id === suiteId);
  expect(suiteRuns.filter(run => run.state === 'cancelled')).toHaveLength(1);
  expect(suiteRuns.filter(run => run.report?.passed)).toHaveLength(2);
  for (const run of suiteRuns.filter(run => run.report?.passed)) {
    expect(run.report.cases.every(item => item.episode_id && item.steps === 500 && item.passed)).toBe(true);
  }

  // Register a second, genuinely enrolled device with the visual profile. This
  // checks registration/display only; this CPU reference worker does not serve it.
  const robotPanel = page.locator('[aria-labelledby="robots-heading"]');
  await robotPanel.locator('summary', { hasText: 'Connect a simulator' }).click();
  await page.getByLabel('Simulator name', { exact: true }).fill('Camera simulator registration');
  const enrollmentResponse = page.waitForResponse(response => response.url().endsWith('/api/platform/enrollments') && response.request().method() === 'POST');
  await page.getByRole('button', { name: 'Create enrollment command', exact: true }).click();
  const enrollment = await (await enrollmentResponse).json();
  const claim = await fetch(`${connection.api_url}/api/agent/v1/enroll`, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
      enrollment_token: enrollment.token, request_id: randomUUID(), secret_hash: randomBytes(32).toString('hex'),
      name: 'Camera simulator registration', simulated: true,
    }) });
  expect(claim.ok).toBe(true);
  const visualDevice = await claim.json();
  const deviceSelect = page.getByRole('combobox', { name: 'Enrolled device', exact: true });
  await expect(deviceSelect).toContainText(visualDevice.device_id, { timeout: 10000 });
  await deviceSelect.selectOption(visualDevice.device_id);
  await page.getByLabel('Robot name', { exact: true }).fill('Camera simulator');
  const visualProfile = 'metaworld-smolvla-pick-place-rgb-v1';
  await page.getByRole('combobox', { name: 'Simulator profile', exact: true }).selectOption(visualProfile);
  await page.getByRole('button', { name: 'Register robot', exact: true }).click();
  await expect(robotPanel.locator('.console-note').first()).toContainText(visualProfile);
  await expect(startMission).toBeDisabled();
  await robotPanel.locator('summary', { hasText: 'Connect a simulator' }).click();
  const recordedVisual = JSON.parse(await fs.readFile(path.join(website, '../examples/manipulation/evidence/smolvla-managed-seed0.json'), 'utf8'));
  await page.locator('summary', { hasText: 'Register a release manifest' }).click();
  await page.getByLabel('Manifest JSON', { exact: true }).fill(JSON.stringify(recordedVisual.manifest));
  await page.getByRole('button', { name: 'Register release', exact: true }).click();
  const releasePanel = page.locator('[aria-labelledby="application-heading"]');
  await expect(releasePanel.locator('.console-note').first()).toContainText(visualProfile);
  await expect(releasePanel.locator('.console-note').first()).toContainText(recordedVisual.manifest.policy.runtime);
  await expect(evaluate).toBeDisabled(); // the existing suite requires state observations
  await page.locator('summary', { hasText: 'Register a release manifest' }).click();
  await page.screenshot({ path: path.join(output, 'visual-registration.png'), fullPage: true });
  await page.getByRole('combobox', { name: 'Selected robot', exact: true }).selectOption(connection.robot_id);
  await releaseSelect.selectOption(connection.release_id);
  await expect(deploy).toBeEnabled();
  await page.screenshot({ path: path.join(output, 'evaluation-console.png'), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(false);
  await page.screenshot({ path: path.join(output, 'evaluation-console-mobile.png'), fullPage: true });
  await page.getByRole('button', { name: 'Sign out', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  expect(failures).toEqual([]);
  Object.assign(evidence, { status: 'passed', suite_id: suiteId, baseline_id: baselineId, candidate_id: candidateId,
    runs: suiteRuns, checks: ['immutable suite creation', 'explicit application gate', 'authoritative robot reservation',
      'Deploy/Start disabled while reserved', 'cancellation requested then acknowledged', 'two real two-seed evaluations',
      'paired report comparison', 'explicit promotion then ordinary successful mission', 'stale qualification response cannot enable another release',
      'promotion survives reload', 'visual-profile robot registration and manifest display (no visual execution)', 'mobile without overflow', 'logout'] });
  console.log(JSON.stringify({ status: evidence.status, checks: evidence.checks }));
} finally {
  if (browser) await browser.close();
  for (const child of processes.reverse()) {
    child.kill('SIGTERM');
    const force = setTimeout(() => child.kill('SIGKILL'), 5000);
    if (child.exitCode === null) await new Promise(resolve => child.once('exit', resolve));
    clearTimeout(force);
  }
  await fs.writeFile(path.join(output, 'result.json'), JSON.stringify(evidence, null, 2));
  await fs.writeFile(path.join(output, 'process.log'), processLog);
}
