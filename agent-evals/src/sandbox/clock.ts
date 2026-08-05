/**
 * SimClock — the harness-owned simulated clock. The sole authority on domain
 * time inside a sandbox instance; only the DES driver (runUntil) advances it.
 * Monotonic by contract: advancing backwards is a harness bug, so it throws.
 */

import type { SimClock } from './api.ts';

export function createSimClock(start: Date): SimClock {
  let current = new Date(start.getTime());
  if (Number.isNaN(current.getTime())) {
    throw new Error(`createSimClock: invalid start date`);
  }
  return {
    now(): Date {
      return new Date(current.getTime());
    },
    advanceTo(t: Date): void {
      if (Number.isNaN(t.getTime())) {
        throw new Error('SimClock.advanceTo: invalid date');
      }
      if (t.getTime() < current.getTime()) {
        throw new Error(
          `SimClock is monotonic: cannot advance to ${t.toISOString()} ` +
            `(now is ${current.toISOString()})`
        );
      }
      current = new Date(t.getTime());
    },
  };
}
