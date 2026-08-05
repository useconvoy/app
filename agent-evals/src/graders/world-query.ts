/**
 * Bundle-backed world query — the replay-mode stand-in for WorldStore.query().
 * Same path grammar as src/schema/match.ts resolvePath, over the root shape:
 *
 *   records:  { [collection]: { [id]: fields } }
 *   messages: { sent: WorldMessage[], inbound: WorldMessage[], all: WorldMessage[] }
 *   files:    { [id]: WorldFile }
 *
 * e.g. "records.policy.POL-1042.renewal_status", "messages.sent.*.to",
 * "files.*.name".
 */

import type { WorldBundle, WorldFile } from '../sandbox/api.ts';
import { resolvePath } from '../schema/match.ts';

export function buildQueryRoot(bundle: WorldBundle): Record<string, unknown> {
  const records: Record<string, Record<string, unknown>> = {};
  for (const r of bundle.records) {
    (records[r.collection] ??= {})[r.id] = r.fields;
  }
  const files: Record<string, WorldFile> = {};
  for (const f of bundle.files) files[f.id] = f;
  return {
    records,
    messages: {
      sent: bundle.messages.filter((m) => m.direction === 'outbound'),
      inbound: bundle.messages.filter((m) => m.direction === 'inbound'),
      all: bundle.messages,
    },
    files,
  };
}

/** GradeRecord.worldQuery implementation for replay grading. */
export function makeBundleQuery(bundle: WorldBundle): (q: string) => unknown[] {
  const root = buildQueryRoot(bundle);
  return (q: string) => resolvePath(root, q);
}
