/**
 * Calendar emulator — how the agent perceives "today". The date comes from
 * the sim clock (via SimWorldStore.clockRef), never the wall clock, which is
 * the property that lets a 45-day mission run in seconds.
 */

import type { ToolEmulator } from '../api.ts';
import { worldClock } from './util.ts';

export const calendarEmulator: ToolEmulator[] = [
  {
    tool: 'calendar.today',
    effectful: false,
    handler(_args, world) {
      const now = worldClock(world).now();
      return { today: now.toISOString().slice(0, 10), ts: now.toISOString() };
    },
  },
];
