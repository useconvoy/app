/**
 * Trigger configuration per routine: schedule, manual, event; a
 * routine's triggers are properties of the one object. Kept in process
 * over fixture defaults until the scheduler and event sources exist.
 *
 * TODO(environments-E0): event sources are designed with environments/
 * (what besides a human may start a run); the store and its plain
 * descriptions move behind that service when it lands.
 */
import "server-only";

export interface RoutineTriggers {
  /** Plain schedule text, e.g. "Mondays at 9am"; absent when unscheduled. */
  schedule?: { description: string };
  /** Whether people may start this routine on demand. */
  manual: boolean;
  /** Plain event description; the source wiring does not exist yet. */
  event?: { description: string };
}

const FIXTURE_TRIGGERS: Record<string, RoutineTriggers> = {
  "routine-access-review": {
    schedule: { description: "First Monday of each quarter at 9am" },
    manual: true,
  },
  "routine-vendor-check": {
    schedule: { description: "Mondays at 9am" },
    manual: true,
  },
  "routine-attestation-chase": {
    manual: true,
    event: { description: "When a new policy version is published" },
  },
};

declare global {
  var __convoyTriggerStore: Map<string, RoutineTriggers> | undefined;
}

function store(): Map<string, RoutineTriggers> {
  if (!globalThis.__convoyTriggerStore) {
    globalThis.__convoyTriggerStore = new Map();
  }
  return globalThis.__convoyTriggerStore;
}

function key(orgId: string, routineId: string): string {
  return `${orgId}:${routineId}`;
}

export function getTriggers(orgId: string, routineId: string): RoutineTriggers {
  return store().get(key(orgId, routineId)) ?? FIXTURE_TRIGGERS[routineId] ?? { manual: true };
}

/** Overwrite the schedule text; edits are operator-gated by the caller. */
export function setTriggerSchedule(orgId: string, routineId: string, description: string): void {
  const current = getTriggers(orgId, routineId);
  store().set(key(orgId, routineId), {
    ...current,
    schedule: description ? { description } : undefined,
  });
}
