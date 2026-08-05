/**
 * AMS emulator — a minimal agency-management-system surface over the `policy`
 * collection. Reads are pure; update_policy is the one effectful mutation.
 * "Expiring within N days" is judged against the SIM clock, never wall time.
 */

import { z } from 'zod';
import type { ToolEmulator } from '../api.ts';
import { worldClock } from './util.ts';

const ListExpiringArgs = z.object({ withinDays: z.number().int().nonnegative() });
const GetPolicyArgs = z.object({ policyId: z.string() });
const UpdatePolicyArgs = z.object({
  policyId: z.string(),
  fields: z.record(z.string(), z.unknown()),
});

const DAY_MS = 86_400_000;

/** Parse a date-only or full ISO string; NaN-safe. */
function parseTs(v: unknown): number | null {
  if (typeof v !== 'string') return null;
  const t = Date.parse(v.length === 10 ? `${v}T00:00:00Z` : v);
  return Number.isNaN(t) ? null : t;
}

export const amsEmulator: ToolEmulator[] = [
  {
    tool: 'ams.list_expiring',
    effectful: false,
    handler(args, world) {
      const a = ListExpiringArgs.parse(args);
      const now = worldClock(world).now().getTime();
      const horizon = now + a.withinDays * DAY_MS;
      const policies = world
        .listRecords('policy')
        .filter((r) => {
          const t = parseTs(r.fields['expiring_date']);
          return t !== null && t <= horizon;
        })
        .map((r) => ({ policyId: r.id, ...r.fields }));
      return { policies };
    },
  },
  {
    tool: 'ams.get_policy',
    effectful: false,
    handler(args, world) {
      const a = GetPolicyArgs.parse(args);
      const record = world.getRecord('policy', a.policyId);
      if (!record) throw new Error(`ams.get_policy: no such policy: ${a.policyId}`);
      // Flattened shape — executors read policy.insured_email etc. directly.
      return { policyId: record.id, ...record.fields, updatedAt: record.updatedAt };
    },
  },
  {
    tool: 'ams.update_policy',
    effectful: true,
    handler(args, world) {
      const a = UpdatePolicyArgs.parse(args);
      if (!world.getRecord('policy', a.policyId)) {
        throw new Error(`ams.update_policy: no such policy: ${a.policyId}`);
      }
      const record = world.upsertRecord('policy', a.policyId, a.fields);
      return { policyId: record.id, fields: record.fields, updatedAt: record.updatedAt };
    },
  },
];
