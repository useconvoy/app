/**
 * store.ts — corpus loading + validation.
 *
 * Loads scenarios, sealed answer keys (with content-hash verification),
 * eval-set configs (resolving scenario ids to files under scenarios/), and the
 * quarantine list. Pure fs + zod; no sibling-component dependencies, so the
 * CLI's lint/report paths work even before the sandbox/executors land.
 */

import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { basename, dirname, isAbsolute, join, resolve } from 'node:path';
import {
  AnswerKeySchema,
  EvalSetConfigSchema,
  ScenarioSchema,
  type AnswerKey,
  type EvalSetConfig,
  type Scenario,
} from '../schema/scenario.ts';

// ---------------------------------------------------------------------------
// Hashing
// ---------------------------------------------------------------------------

export function sha256Hex(content: string): string {
  return createHash('sha256').update(content).digest('hex');
}

/** Strip an optional "sha256:" / "sha256-" prefix and lowercase. */
export function normalizeHash(hash: string): string {
  return hash.replace(/^sha256[:-]/i, '').toLowerCase();
}

// ---------------------------------------------------------------------------
// JSON reading with pathful errors
// ---------------------------------------------------------------------------

function readJson(path: string): unknown {
  let text: string;
  try {
    text = readFileSync(path, 'utf8');
  } catch (err) {
    throw new Error(`cannot read ${path}: ${err instanceof Error ? err.message : String(err)}`);
  }
  try {
    return JSON.parse(text);
  } catch (err) {
    throw new Error(`invalid JSON in ${path}: ${err instanceof Error ? err.message : String(err)}`);
  }
}

// ---------------------------------------------------------------------------
// Scenario
// ---------------------------------------------------------------------------

export function loadScenario(path: string): Scenario {
  const parsed = ScenarioSchema.safeParse(readJson(path));
  if (!parsed.success) {
    throw new Error(`scenario ${path} failed validation: ${parsed.error.message}`);
  }
  return parsed.data;
}

// ---------------------------------------------------------------------------
// Answer key
// ---------------------------------------------------------------------------

export interface AnswerKeyFile {
  key: AnswerKey;
  /** sha256 hex of the raw file bytes. */
  contentHash: string;
  /** sha256 hex of the re-serialized (JSON.stringify) parsed content. */
  canonicalHash: string;
}

export function loadAnswerKeyFile(path: string): AnswerKeyFile {
  let raw: string;
  try {
    raw = readFileSync(path, 'utf8');
  } catch (err) {
    throw new Error(`cannot read answer key ${path}: ${err instanceof Error ? err.message : String(err)}`);
  }
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch (err) {
    throw new Error(`invalid JSON in answer key ${path}: ${err instanceof Error ? err.message : String(err)}`);
  }
  const parsed = AnswerKeySchema.safeParse(json);
  if (!parsed.success) {
    throw new Error(`answer key ${path} failed validation: ${parsed.error.message}`);
  }
  return { key: parsed.data, contentHash: sha256Hex(raw), canonicalHash: sha256Hex(JSON.stringify(json)) };
}

/** True when the expected hash matches either the raw or canonical content hash. */
export function answerKeyHashMatches(file: AnswerKeyFile, expectedHash: string): boolean {
  const want = normalizeHash(expectedHash);
  return want === file.contentHash || want === file.canonicalHash;
}

/**
 * Load an answer key; when `expectedHash` is given, verify it against the file
 * content and WARN (not throw) on mismatch — a drifted hash is an authoring
 * bug the lint command turns into a hard error.
 */
export function loadAnswerKey(
  path: string,
  expectedHash?: string,
  warn: (msg: string) => void = (m) => console.warn(m)
): AnswerKey {
  const file = loadAnswerKeyFile(path);
  if (expectedHash !== undefined && !answerKeyHashMatches(file, expectedHash)) {
    warn(
      `[store] answer key hash mismatch for ${path}: scenario expects ${expectedHash}, ` +
        `content is sha256:${file.contentHash}`
    );
  }
  return file.key;
}

// ---------------------------------------------------------------------------
// Eval set
// ---------------------------------------------------------------------------

export interface LoadedEvalSet {
  config: EvalSetConfig;
  setPath: string;
  /** The scenarios/ directory the set's ids resolve against. */
  scenariosDir: string;
  /** scenario id → absolute scenario file path (resolved ids only). */
  scenarioPaths: Map<string, string>;
  /** Ids listed in the set with no matching *.scenario.json file. */
  missing: string[];
}

/**
 * Load an eval-set config and resolve each scenario id to a file. Sets live at
 * scenarios/sets/<name>.json, scenarios at scenarios/<id>.scenario.json —
 * filename convention first, then a content scan matching the declared `id`.
 */
export function loadEvalSet(path: string): LoadedEvalSet {
  const setPath = resolve(path);
  const parsed = EvalSetConfigSchema.safeParse(readJson(setPath));
  if (!parsed.success) {
    throw new Error(`eval set ${setPath} failed validation: ${parsed.error.message}`);
  }
  const config = parsed.data;
  const setDir = dirname(setPath);
  const scenariosDir = basename(setDir) === 'sets' ? dirname(setDir) : setDir;

  const byStem = new Map<string, string>();
  if (existsSync(scenariosDir)) {
    for (const f of readdirSync(scenariosDir)) {
      if (!f.endsWith('.scenario.json')) continue;
      byStem.set(f.slice(0, -'.scenario.json'.length), join(scenariosDir, f));
    }
  }

  const scenarioPaths = new Map<string, string>();
  const missing: string[] = [];
  for (const id of config.scenarios) {
    let p = byStem.get(id);
    if (!p) {
      for (const candidate of byStem.values()) {
        try {
          const json = readJson(candidate);
          if (typeof json === 'object' && json !== null && (json as { id?: unknown }).id === id) {
            p = candidate;
            break;
          }
        } catch {
          // unreadable candidate — skip; lint reports it via schema check
        }
      }
    }
    if (p) scenarioPaths.set(id, p);
    else missing.push(id);
  }
  return { config, setPath, scenariosDir, scenarioPaths, missing };
}

/**
 * Resolve a scenario's answerKeyRef.path. Tried in order: absolute; relative
 * to the scenario file; relative to scenarios/; scenarios/keys/<basename>.
 */
export function resolveAnswerKeyPath(ref: string, scenarioPath: string, scenariosDir: string): string {
  if (isAbsolute(ref)) return ref;
  const candidates = [
    join(dirname(scenarioPath), ref),
    join(scenariosDir, ref),
    join(scenariosDir, 'keys', basename(ref)),
  ];
  for (const c of candidates) if (existsSync(c)) return c;
  // Best guess — loadAnswerKeyFile will raise a clear ENOENT-style error.
  return candidates[1] ?? ref;
}

// ---------------------------------------------------------------------------
// Quarantine — {quarantined: string[]} in a tiny YAML file. No YAML dep in
// the project, so this is a deliberately minimal parser for exactly that
// shape (block list, inline list, quoted strings, comments, `- id: x` rows).
// ---------------------------------------------------------------------------

export interface Quarantine {
  quarantined: string[];
}

function unquote(v: string): string {
  const t = v.trim();
  if ((t.startsWith('"') && t.endsWith('"')) || (t.startsWith("'") && t.endsWith("'"))) {
    return t.slice(1, -1);
  }
  return t;
}

export function parseQuarantineYaml(text: string): Quarantine {
  const quarantined: string[] = [];
  let inList = false;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.replace(/(^|\s)#.*$/, '').trimEnd();
    if (!line.trim()) continue;
    const head = line.match(/^quarantined:\s*(.*)$/);
    if (head) {
      const rest = (head[1] ?? '').trim();
      if (rest.startsWith('[')) {
        const inner = rest.replace(/^\[/, '').replace(/\]\s*$/, '');
        for (const part of inner.split(',')) {
          const v = unquote(part);
          if (v) quarantined.push(v);
        }
        inList = false;
      } else {
        inList = true;
      }
      continue;
    }
    if (inList) {
      const item = line.trim().match(/^-\s*(.+)$/);
      if (item) {
        let v = unquote(item[1] ?? '');
        // tolerate `- id: renewal-x` rows (richer quarantine entries)
        const idField = v.match(/^id:\s*(.+)$/);
        if (idField) v = unquote(idField[1] ?? '');
        if (v) quarantined.push(v);
      } else if (!/^\s/.test(line)) {
        inList = false; // a new top-level key ends the block list
      }
    }
  }
  return { quarantined };
}

/** Missing file → empty quarantine (the corpus may not ship one yet). */
export function loadQuarantine(path: string): Quarantine {
  if (!existsSync(path)) return { quarantined: [] };
  return parseQuarantineYaml(readFileSync(path, 'utf8'));
}
