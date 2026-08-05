/**
 * Shared emulator helpers. Emulator handlers only receive the WorldStore, but
 * time-perceiving tools (calendar.today, ams.list_expiring) need the sim
 * clock — the SimWorldStore extension carries it (see world.ts).
 */

import type { ClockPort } from '../../runtime/ports.ts';
import type { WorldStore } from '../api.ts';
import type { SimWorldStore } from '../world.ts';

export function worldClock(world: WorldStore): ClockPort {
  const clock = (world as SimWorldStore).clockRef;
  if (!clock) {
    throw new Error('emulator requires a SimWorldStore (createWorldStore) — clockRef missing');
  }
  return clock;
}

/** The agent's own address in the sim — the from: on outbound email. */
export const AGENT_ADDRESS = 'agent@convoy.sim';

/** Thread key from a subject line: strip reply prefixes, normalize. */
export function threadIdForSubject(subject: string): string {
  return 'thread_' + subject.replace(/^\s*((re|fwd?):\s*)+/i, '').trim().toLowerCase().replace(/\s+/g, '-');
}
