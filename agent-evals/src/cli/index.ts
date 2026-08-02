#!/usr/bin/env node
/**
 * convoy-evals — the eval-harness CLI. No arg-parsing deps; terse stdout;
 * --json on run/replay for machine output.
 *
 *   run      --set <path> --subject scripted:golden [--filter id,id] [--trials N]
 *            [--usd-cap X] [--out dir] [--judge live|cache-only] [--concurrency N] [--json]
 *   replay   --set <path> --records <dir> [--json]
 *   report   --result <suite-result.json> [--set <path>] [--rehearsal <scenarioId> --trial N]
 *   lint     --set <path>
 *   dry-run  --set <path> [--scenario id]
 *
 * run/replay/dry-run import the runner lazily so lint/report keep working
 * before the sandbox/executors/graders/scoring siblings land.
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { GateKindSchema } from '../runtime/events.ts';
import { resolvePath } from '../schema/match.ts';
import type { AnswerKey, Scenario } from '../schema/scenario.ts';
import { SuiteResultSchema, type SuiteResult } from '../schema/verdict.ts';
import type { WorldBundle } from '../sandbox/api.ts';
import { renderRehearsalReport } from '../reports/rehearsal-report.ts';
import { writeSuiteReport } from '../reports/suite-report.ts';
import {
  answerKeyHashMatches,
  loadAnswerKeyFile,
  loadEvalSet,
  loadScenario,
  resolveAnswerKeyPath,
  type AnswerKeyFile,
  type LoadedEvalSet,
} from '../runner/store.ts';
import {
  defaultOutDir,
  eventsFileName,
  readJsonlEvents,
  timestampSlug,
  worldFileName,
} from '../runner/trial-utils.ts';

// ---------------------------------------------------------------------------
// Arg parsing
// ---------------------------------------------------------------------------

type Flags = Map<string, string | true>;

class CliError extends Error {}

function parseArgs(argv: string[]): { cmd: string | undefined; flags: Flags } {
  const [cmd, ...rest] = argv;
  const flags: Flags = new Map();
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (a === undefined) break;
    if (!a.startsWith('--')) throw new CliError(`unexpected argument: ${a}`);
    const name = a.slice(2);
    const next = rest[i + 1];
    if (next !== undefined && !next.startsWith('--')) {
      flags.set(name, next);
      i++;
    } else {
      flags.set(name, true);
    }
  }
  return { cmd, flags };
}

function req(flags: Flags, name: string): string {
  const v = flags.get(name);
  if (typeof v !== 'string' || v.length === 0) throw new CliError(`missing required flag --${name} <value>`);
  return v;
}

function optStr(flags: Flags, name: string): string | undefined {
  const v = flags.get(name);
  return typeof v === 'string' ? v : undefined;
}

function optNum(flags: Flags, name: string): number | undefined {
  const v = optStr(flags, name);
  if (v === undefined) return undefined;
  const n = Number(v);
  if (!Number.isFinite(n)) throw new CliError(`--${name} expects a number, got "${v}"`);
  return n;
}

function optJudge(flags: Flags): 'live' | 'cache-only' | undefined {
  const v = optStr(flags, 'judge');
  if (v === undefined) return undefined;
  if (v !== 'live' && v !== 'cache-only') throw new CliError(`--judge expects live|cache-only, got "${v}"`);
  return v;
}

// ---------------------------------------------------------------------------
// Output helpers
// ---------------------------------------------------------------------------

function table(headers: string[], rows: string[][]): string {
  const widths = headers.map((h, i) => Math.max(h.length, ...rows.map((r) => (r[i] ?? '').length)));
  const line = (cells: string[]): string => cells.map((c, i) => (c ?? '').padEnd(widths[i] ?? 0)).join('  ');
  return [line(headers), line(widths.map((w) => '-'.repeat(w))), ...rows.map((r) => line(r))].join('\n');
}

function printSuite(result: SuiteResult, asJson: boolean): void {
  if (asJson) {
    console.log(JSON.stringify(result));
    return;
  }
  const rows = result.scenarios.map((s) => [
    s.scenarioId,
    s.status,
    s.invariantViolation ? 'VIOLATED' : 'held',
    String(s.trials.length),
    `$${s.totalCostUsd.toFixed(4)}`,
    s.meanDecay ? s.meanDecay.slope.toFixed(3) : '-',
    s.meanDecay ? s.meanDecay.auc.toFixed(2) : '-',
    s.meanDecay ? s.meanDecay.minQ.toFixed(2) : '-',
  ]);
  console.log(table(['scenario', 'status', 'invariants', 'trials', 'cost', 'slope', 'auc', 'minQ'], rows));
  console.log('');
  console.log(
    `${result.green ? 'verdict: GREEN' : 'verdict: NOT GREEN'} — ${result.evalSet.name}@${result.evalSet.version}` +
      ` subject=${result.subject.label} total=$${result.totalCostUsd.toFixed(4)}`
  );
  for (const d of result.greenDetail) console.log(`  - ${d}`);
}

// ---------------------------------------------------------------------------
// run
// ---------------------------------------------------------------------------

async function cmdRun(flags: Flags): Promise<number> {
  const setPath = req(flags, 'set');
  const subjectSpec = req(flags, 'subject');
  const loaded = loadEvalSet(setPath);
  const outDir = optStr(flags, 'out') ?? defaultOutDir();
  const asJson = flags.get('json') === true;

  const { runSuite } = await import('../runner/suite-runner.ts');
  let subject: Parameters<typeof runSuite>[0]['subject'];
  if (subjectSpec.startsWith('scripted:')) {
    subject = { kind: 'scripted', executor: subjectSpec.slice('scripted:'.length) };
  } else if (subjectSpec === 'baseline') {
    const { createBaselineRuntimeFactory } = await import('../executors/index.ts');
    subject = { kind: 'runtime', factory: createBaselineRuntimeFactory({}), label: 'baseline' };
  } else {
    throw new CliError(`--subject expects scripted:<executor> or baseline, got "${subjectSpec}"`);
  }

  const filterRaw = optStr(flags, 'filter');
  const result = await runSuite({
    evalSetPath: setPath,
    subject,
    scenarioFilter: filterRaw ? filterRaw.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
    trialsOverride: optNum(flags, 'trials'),
    usdCap: optNum(flags, 'usd-cap'),
    maxConcurrent: optNum(flags, 'concurrency'),
    judgeMode: optJudge(flags),
    outDir,
  });

  const reportPath = join(outDir, 'suite-report.html');
  writeSuiteReport(result, reportPath, { itemFloor: loaded.config.thresholds.itemFloor });
  printSuite(result, asJson);
  if (!asJson) console.log(`\nartifacts: ${outDir}\nreport: ${reportPath}`);
  return result.green ? 0 : 1;
}

// ---------------------------------------------------------------------------
// replay
// ---------------------------------------------------------------------------

async function cmdReplay(flags: Flags): Promise<number> {
  const setPath = req(flags, 'set');
  const recordsDir = req(flags, 'records');
  const asJson = flags.get('json') === true;
  const { replaySuite } = await import('../runner/replay.ts');
  const result = await replaySuite({ evalSetPath: setPath, recordsDir, judgeMode: optJudge(flags) });
  printSuite(result, asJson);
  return result.green ? 0 : 1;
}

// ---------------------------------------------------------------------------
// report
// ---------------------------------------------------------------------------

function scenarioFileFor(id: string, setPath: string | undefined): Scenario {
  if (setPath) {
    const loaded = loadEvalSet(setPath);
    const p = loaded.scenarioPaths.get(id);
    if (p) return loadScenario(p);
  }
  // conventional fallback: ./scenarios/<id>.scenario.json
  return loadScenario(resolve('scenarios', `${id}.scenario.json`));
}

function cmdReport(flags: Flags): number {
  const resultPath = resolve(req(flags, 'result'));
  const parsed = SuiteResultSchema.safeParse(JSON.parse(readFileSync(resultPath, 'utf8')));
  if (!parsed.success) throw new CliError(`${resultPath} is not a valid suite-result.json: ${parsed.error.message}`);
  const result = parsed.data;
  const dir = dirname(resultPath);
  const setPath = optStr(flags, 'set');
  const itemFloor = setPath ? loadEvalSet(setPath).config.thresholds.itemFloor : undefined;

  const suitePath = join(dir, 'suite-report.html');
  writeSuiteReport(result, suitePath, itemFloor !== undefined ? { itemFloor } : {});
  console.log(`wrote ${suitePath}`);

  const rehearsalId = optStr(flags, 'rehearsal');
  if (rehearsalId) {
    const trialIdx = optNum(flags, 'trial') ?? 0;
    const sv = result.scenarios.find((s) => s.scenarioId === rehearsalId);
    if (!sv) throw new CliError(`scenario "${rehearsalId}" not present in ${resultPath}`);
    const trial = sv.trials.find((t) => t.trialIdx === trialIdx);
    if (!trial) throw new CliError(`trial ${trialIdx} of "${rehearsalId}" not present (have ${sv.trials.length} trial(s))`);
    const scenario = scenarioFileFor(rehearsalId, setPath);
    const events = readJsonlEvents(join(dir, eventsFileName(rehearsalId, trialIdx)));
    const world = JSON.parse(readFileSync(join(dir, worldFileName(rehearsalId, trialIdx)), 'utf8')) as WorldBundle;
    const html = renderRehearsalReport({ scenario, trial, events, world });
    const outPath = join(dir, `rehearsal-${rehearsalId}-t${trialIdx}.html`);
    writeFileSync(outPath, html);
    console.log(`wrote ${outPath}`);
  }
  return 0;
}

// ---------------------------------------------------------------------------
// lint — static completeness per scenario.
// ---------------------------------------------------------------------------

interface LintRow {
  scenario: string;
  check: string;
  ok: boolean;
  detail: string;
}

/** Deep-walk arbitrary JSON collecting {$key: "..."} refs. */
function collectKeyRefs(value: unknown, out: string[]): void {
  if (Array.isArray(value)) {
    for (const v of value) collectKeyRefs(v, out);
    return;
  }
  if (value !== null && typeof value === 'object') {
    const obj = value as Record<string, unknown>;
    if (typeof obj['$key'] === 'string' && Object.keys(obj).length === 1) {
      out.push(obj['$key']);
      return;
    }
    for (const v of Object.values(obj)) collectKeyRefs(v, out);
  }
}

/**
 * A KeyRef resolves if its path hits the key root, facts, or a perItem
 * subtree. Corpus convention: "item.<field>" refs are item-relative — they
 * resolve inside the current item's perItem subtree with the prefix stripped
 * (checked here against every perItem entry; a typo'd field matches none).
 */
function keyRefResolves(key: AnswerKey, keyPath: string): boolean {
  const perItem = Object.values(key.perItem ?? {});
  if (keyPath.startsWith('item.')) {
    const rest = keyPath.slice('item.'.length);
    return perItem.some((subtree) => resolvePath(subtree, rest).length > 0);
  }
  if (resolvePath(key, keyPath).length > 0) return true;
  if (resolvePath(key.facts, keyPath).length > 0) return true;
  return perItem.some((subtree) => resolvePath(subtree, keyPath).length > 0);
}

function lintScenario(id: string, loaded: LoadedEvalSet, rows: LintRow[]): void {
  const row = (check: string, ok: boolean, detail = ''): void => {
    rows.push({ scenario: id, check, ok, detail });
  };

  const path = loaded.scenarioPaths.get(id);
  if (!path) {
    row('file', false, `no ${id}.scenario.json under ${loaded.scenariosDir}`);
    return;
  }
  let scenario: Scenario;
  try {
    scenario = loadScenario(path);
    row('schema', true);
  } catch (err) {
    row('schema', false, err instanceof Error ? err.message.split('\n')[0] ?? '' : String(err));
    return;
  }

  row('budgets', Boolean(scenario.budgets && scenario.budgets.usd > 0 && scenario.budgets.simTime), '');

  let keyFile: AnswerKeyFile | undefined;
  const keyPath = resolveAnswerKeyPath(scenario.answerKeyRef.path, path, loaded.scenariosDir);
  try {
    keyFile = loadAnswerKeyFile(keyPath);
    row('answer-key', true);
  } catch (err) {
    row('answer-key', false, err instanceof Error ? err.message.split('\n')[0] ?? '' : String(err));
  }
  if (!keyFile) return;
  const key = keyFile.key;

  row(
    'key-hash',
    answerKeyHashMatches(keyFile, scenario.answerKeyRef.hash),
    answerKeyHashMatches(keyFile, scenario.answerKeyRef.hash)
      ? ''
      : `answerKeyRef.hash ${scenario.answerKeyRef.hash} != sha256:${keyFile.contentHash}`
  );

  // Every KeyRef in graders (+ item graders + template) and key checklists resolves.
  const refs: string[] = [];
  collectKeyRefs(scenario.graders, refs);
  collectKeyRefs(scenario.itemGraderTemplate ?? [], refs);
  for (const item of scenario.items ?? []) {
    if (item.graders !== 'inherit') collectKeyRefs(item.graders, refs);
  }
  collectKeyRefs(key.checklists ?? {}, refs);
  const unresolved = [...new Set(refs)].filter((r) => !keyRefResolves(key, r));
  row('key-refs', unresolved.length === 0, unresolved.length ? `unresolved: ${unresolved.join(', ')}` : `${refs.length} ref(s)`);

  // Checklist refs used by graders exist in the key.
  const checklistRefs: string[] = [];
  const scanAsserts = (graders: unknown): void => {
    if (!Array.isArray(graders)) return;
    for (const g of graders) {
      const asserts = (g as { asserts?: unknown }).asserts;
      if (!Array.isArray(asserts)) continue;
      for (const a of asserts) {
        const obj = a as { kind?: unknown; checklistRef?: unknown };
        if (obj.kind === 'checklist' && typeof obj.checklistRef === 'string') checklistRefs.push(obj.checklistRef);
      }
    }
  };
  scanAsserts(scenario.graders);
  scanAsserts(scenario.itemGraderTemplate ?? []);
  for (const item of scenario.items ?? []) if (item.graders !== 'inherit') scanAsserts(item.graders);
  const missingChecklists = [...new Set(checklistRefs)].filter((r) => !(key.checklists && r in key.checklists));
  row(
    'checklists',
    missingChecklists.length === 0,
    missingChecklists.length ? `missing: ${missingChecklists.join(', ')}` : `${checklistRefs.length} ref(s)`
  );

  // Scripted approval steps' gate kinds are valid GateKinds.
  if (scenario.approvals.mode === 'scripted') {
    const validKinds = new Set<string>(GateKindSchema.options);
    const badKinds = scenario.approvals.steps
      .map((s) => s.expect.kind)
      .filter((k): k is NonNullable<typeof k> => k !== undefined)
      .filter((k) => !validKinds.has(k));
    row('gate-kinds', badKinds.length === 0, badKinds.length ? `invalid: ${badKinds.join(', ')}` : `${scenario.approvals.steps.length} step(s)`);
  } else {
    row('gate-kinds', true, `mode=${scenario.approvals.mode}`);
  }

  // Gauntlet items: keyRef exists in perItem; 'inherit' requires a template.
  if (scenario.items && scenario.items.length > 0) {
    const perItem = key.perItem ?? {};
    const badItems = scenario.items.filter((i) => !(i.keyRef in perItem)).map((i) => i.itemId);
    row('item-key-refs', badItems.length === 0, badItems.length ? `no perItem entry: ${badItems.join(', ')}` : `${scenario.items.length} item(s)`);
    const inheriting = scenario.items.some((i) => i.graders === 'inherit');
    if (inheriting) {
      row(
        'item-grader-template',
        Boolean(scenario.itemGraderTemplate && scenario.itemGraderTemplate.length > 0),
        scenario.itemGraderTemplate?.length ? '' : "items use graders:'inherit' but itemGraderTemplate is missing/empty"
      );
    }
  }
}

function cmdLint(flags: Flags): number {
  const setPath = req(flags, 'set');
  const loaded = loadEvalSet(setPath);
  const rows: LintRow[] = [];
  for (const id of loaded.config.scenarios) lintScenario(id, loaded, rows);

  console.log(
    table(
      ['scenario', 'check', 'result', 'detail'],
      rows.map((r) => [r.scenario, r.check, r.ok ? 'ok' : 'PROBLEM', r.detail])
    )
  );
  const problems = rows.filter((r) => !r.ok).length;
  console.log(`\n${problems === 0 ? 'lint: clean' : `lint: ${problems} problem(s)`} — ${loaded.config.name}@${loaded.config.version}, ${loaded.config.scenarios.length} scenario(s)`);
  return problems === 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------
// dry-run — a scenario must pass under scripted:golden AND fail under
// scripted:violator-no-gate, or it cannot distinguish the two and is rejected.
// ---------------------------------------------------------------------------

async function cmdDryRun(flags: Flags): Promise<number> {
  const setPath = req(flags, 'set');
  const only = optStr(flags, 'scenario');
  const filter = only ? [only] : undefined;
  const { runSuite } = await import('../runner/suite-runner.ts');
  const base = join('results', `dry-run-${timestampSlug()}`);

  const golden = await runSuite({
    evalSetPath: setPath,
    subject: { kind: 'scripted', executor: 'golden' },
    scenarioFilter: filter,
    trialsOverride: 1,
    judgeMode: 'cache-only',
    outDir: join(base, 'golden'),
  });
  const violator = await runSuite({
    evalSetPath: setPath,
    subject: { kind: 'scripted', executor: 'violator-no-gate' },
    scenarioFilter: filter,
    trialsOverride: 1,
    judgeMode: 'cache-only',
    outDir: join(base, 'violator-no-gate'),
  });

  const violatorById = new Map(violator.scenarios.map((s) => [s.scenarioId, s]));
  let rejected = 0;
  const rows = golden.scenarios.map((g) => {
    const v = violatorById.get(g.scenarioId);
    const goldenOk = g.status === 'passed' || (g.status === 'quarantined' && !g.invariantViolation);
    const violatorOk = v !== undefined && (v.status === 'failed' || (v.status === 'quarantined' && v.invariantViolation));
    const ok = goldenOk && violatorOk;
    if (!ok) rejected++;
    const why = ok
      ? 'distinguishes golden from violator'
      : !goldenOk
        ? `golden did not pass (${g.status})`
        : `violator did not fail (${v ? v.status : 'missing'})`;
    return [g.scenarioId, g.status, v ? v.status : 'missing', ok ? 'OK' : 'REJECTED', why];
  });
  console.log(table(['scenario', 'golden', 'violator-no-gate', 'verdict', 'detail'], rows));
  console.log(`\ndry-run: ${rejected === 0 ? 'all scenarios accepted' : `${rejected} scenario(s) REJECTED`} (artifacts: ${base})`);
  return rejected === 0 ? 0 : 1;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

const USAGE = `convoy-evals <command>

  run      --set <path> --subject scripted:<executor>|baseline [--filter id,id]
           [--trials N] [--usd-cap X] [--out dir] [--judge live|cache-only]
           [--concurrency N] [--json]
  replay   --set <path> --records <dir> [--judge live|cache-only] [--json]
  report   --result <suite-result.json> [--set <path>]
           [--rehearsal <scenarioId> --trial N]
  lint     --set <path>
  dry-run  --set <path> [--scenario id]
`;

async function main(): Promise<void> {
  let code: number;
  try {
    const { cmd, flags } = parseArgs(process.argv.slice(2));
    switch (cmd) {
      case 'run':
        code = await cmdRun(flags);
        break;
      case 'replay':
        code = await cmdReplay(flags);
        break;
      case 'report':
        code = cmdReport(flags);
        break;
      case 'lint':
        code = cmdLint(flags);
        break;
      case 'dry-run':
        code = await cmdDryRun(flags);
        break;
      default:
        process.stdout.write(USAGE);
        code = cmd === undefined || cmd === 'help' ? 0 : 2;
    }
  } catch (err) {
    if (err instanceof CliError) {
      console.error(`error: ${err.message}`);
      code = 2;
    } else {
      console.error(`error: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`);
      code = 2;
    }
  }
  process.exitCode = code;
}

const invokedDirectly = (() => {
  const argv1 = process.argv[1];
  if (!argv1) return false;
  try {
    if (import.meta.url === pathToFileURL(resolve(argv1)).href) return true;
  } catch {
    // fall through
  }
  return argv1.endsWith('cli/index.ts') || argv1.endsWith('convoy-evals');
})();

if (invokedDirectly) void main();
