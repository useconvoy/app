/**
 * Matchers are DATA, not code: JSONPath-lite paths + comparison ops. This is
 * what keeps scenarios serializable, diffable, and capturable.
 *
 * Path grammar (deliberately small): dot segments, numeric indices, `*`
 * wildcard over arrays/objects. Examples:
 *   "to"  "packet.items.3.premium"  "attachments.*.name"
 */

import { z } from 'zod';

export const CmpOpSchema = z.enum([
  'eq', 'neq', 'lt', 'lte', 'gt', 'gte', 'contains', 'regex', 'in', 'exists', 'absent',
]);
export type CmpOp = z.infer<typeof CmpOpSchema>;

/** Resolves into the sealed answer key at grade time — keys never inline in scenarios. */
export const KeyRefSchema = z.object({ $key: z.string() });
export type KeyRef = z.infer<typeof KeyRefSchema>;

const JsonSchema: z.ZodType<unknown> = z.unknown();

export const ClauseSchema = z.object({
  path: z.string(),
  op: CmpOpSchema,
  value: z.union([KeyRefSchema, JsonSchema]).optional(),
});
export type Clause = z.infer<typeof ClauseSchema>;

export const ArgsMatcherSchema = z.object({ all: z.array(ClauseSchema) });
export type ArgsMatcher = z.infer<typeof ArgsMatcherSchema>;

export function isKeyRef(v: unknown): v is KeyRef {
  return typeof v === 'object' && v !== null && '$key' in v && typeof (v as KeyRef).$key === 'string';
}

/** Resolve a dotted path against a value; wildcard fans out. Missing → []. */
export function resolvePath(value: unknown, path: string): unknown[] {
  let current: unknown[] = [value];
  if (path === '' || path === '$') return current;
  for (const seg of path.split('.')) {
    const next: unknown[] = [];
    for (const v of current) {
      if (v === null || typeof v !== 'object') continue;
      if (seg === '*') {
        next.push(...(Array.isArray(v) ? v : Object.values(v)));
      } else if (Array.isArray(v)) {
        const idx = Number(seg);
        if (Number.isInteger(idx) && idx >= 0 && idx < v.length) next.push(v[idx]);
      } else if (seg in (v as Record<string, unknown>)) {
        next.push((v as Record<string, unknown>)[seg]);
      }
    }
    current = next;
  }
  return current;
}

export function compare(op: CmpOp, actual: unknown, expected: unknown): boolean {
  switch (op) {
    case 'exists': return actual !== undefined;
    case 'absent': return actual === undefined;
    case 'eq': return JSON.stringify(actual) === JSON.stringify(expected);
    case 'neq': return JSON.stringify(actual) !== JSON.stringify(expected);
    case 'lt': return typeof actual === 'number' && typeof expected === 'number' && actual < expected;
    case 'lte': return typeof actual === 'number' && typeof expected === 'number' && actual <= expected;
    case 'gt': return typeof actual === 'number' && typeof expected === 'number' && actual > expected;
    case 'gte': return typeof actual === 'number' && typeof expected === 'number' && actual >= expected;
    case 'contains':
      if (typeof actual === 'string') return typeof expected === 'string' && actual.includes(expected);
      if (Array.isArray(actual)) return actual.some((a) => JSON.stringify(a) === JSON.stringify(expected));
      return false;
    case 'regex': return typeof actual === 'string' && typeof expected === 'string' && new RegExp(expected).test(actual);
    case 'in': return Array.isArray(expected) && expected.some((e) => JSON.stringify(e) === JSON.stringify(actual));
  }
}

export type KeyResolver = (keyPath: string) => unknown;

/**
 * Evaluate a matcher against a value. `exists`/`absent` quantify over the path
 * itself; every other op passes if ANY path-resolved value satisfies it
 * (wildcard = existential), and all clauses must hold.
 */
export function matches(matcher: ArgsMatcher, value: unknown, resolveKey?: KeyResolver): boolean {
  return matcher.all.every((clause) => {
    const actuals = resolvePath(value, clause.path);
    const expected = isKeyRef(clause.value)
      ? (resolveKey ?? missingKeyResolver)(clause.value.$key)
      : clause.value;
    if (clause.op === 'exists') return actuals.length > 0;
    if (clause.op === 'absent') return actuals.length === 0;
    return actuals.some((a) => compare(clause.op, a, expected));
  });
}

const missingKeyResolver: KeyResolver = (k) => {
  throw new Error(`KeyRef "${k}" used but no answer key resolver provided`);
};
