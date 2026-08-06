/**
 * world-query-shim.ts — the runtime seam names `bundleQuery` as the export of
 * src/graders/world-query.ts, but the graders component ships it as
 * `makeBundleQuery`. This shim accepts either name so the runner keeps working
 * whichever way the mismatch is resolved.
 */

import * as worldQueryModule from '../graders/world-query.ts';
import type { WorldBundle } from '../sandbox/api.ts';

type BundleQueryFn = (bundle: WorldBundle) => (q: string) => unknown[];

const mod = worldQueryModule as Partial<Record<'bundleQuery' | 'makeBundleQuery', BundleQueryFn>>;
const resolved = mod.bundleQuery ?? mod.makeBundleQuery;
if (resolved === undefined) {
  throw new Error('src/graders/world-query.ts exports neither bundleQuery nor makeBundleQuery');
}

export const bundleQuery: BundleQueryFn = resolved;
