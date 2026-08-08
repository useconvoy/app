/**
 * Trigger configuration per routine: a routine's triggers are properties
 * of the one object. The schedule is plain text on the routines row; every
 * routine can be started on demand in phase 1, and event sources do not
 * exist yet, so neither needs storage of its own.
 *
 * TODO(environments-E0): event sources are designed with environments/
 * (what besides a human may start a run); their descriptions move behind
 * that service when it lands.
 */
import "server-only";

import type { RoutineRecord } from "./queries";

export interface RoutineTriggers {
  /** Plain schedule text, e.g. "Mondays at 9am"; absent when unscheduled. */
  schedule?: { description: string };
  /** Whether people may start this routine on demand. */
  manual: boolean;
  /** Plain event description; the source wiring does not exist yet. */
  event?: { description: string };
}

/** The trigger view of one stored routine. */
export function triggersForRoutine(routine: RoutineRecord): RoutineTriggers {
  return {
    ...(routine.scheduleDescription ? { schedule: { description: routine.scheduleDescription } } : {}),
    manual: true,
  };
}
