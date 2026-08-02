/**
 * EventLog — in-memory append-only event log with JSONL persistence.
 *
 * v1 stand-in for the Postgres events table. The interface is deliberately
 * shaped like the table (append + ordered reads), so swapping in packages/db
 * later is a storage change, not an API change. Graders and scoring consume
 * ONLY this interface plus exported world/artifact bundles.
 */

import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { EventSchema, type ConvoyEvent, type EventInput, type MissionId } from './events.ts';
import type { ClockPort } from './ports.ts';

export class EventLog {
  private events: ConvoyEvent[] = [];
  private seqByMission = new Map<MissionId, number>();
  private listeners: Array<(e: ConvoyEvent) => void> = [];
  private clock: ClockPort;

  constructor(clock: ClockPort) {
    this.clock = clock;
  }

  append(input: EventInput): ConvoyEvent {
    const seq = this.seqByMission.get(input.missionId) ?? 0;
    const event = EventSchema.parse({
      ...input,
      eventId: randomUUID(),
      seq,
      ts: input.ts ?? this.clock.now().toISOString(),
      wallTs: new Date().toISOString(),
    });
    this.seqByMission.set(input.missionId, seq + 1);
    this.events.push(event);
    for (const fn of this.listeners) fn(event);
    return event;
  }

  /** Ordered by (ts, seq) — zero-duration sim steps tie on ts, resolve by append order. */
  forMission(missionId: MissionId): ConvoyEvent[] {
    return this.events
      .filter((e) => e.missionId === missionId)
      .sort((a, b) => (a.ts === b.ts ? a.seq - b.seq : a.ts < b.ts ? -1 : 1));
  }

  all(): ConvoyEvent[] {
    return [...this.events];
  }

  onAppend(fn: (e: ConvoyEvent) => void): void {
    this.listeners.push(fn);
  }

  toJsonl(): string {
    return this.events.map((e) => JSON.stringify(e)).join('\n') + (this.events.length ? '\n' : '');
  }

  writeJsonl(path: string): void {
    writeFileSync(path, this.toJsonl());
  }

  static fromJsonl(path: string, clock: ClockPort): EventLog {
    const log = new EventLog(clock);
    const text = readFileSync(path, 'utf8');
    for (const line of text.split('\n')) {
      if (!line.trim()) continue;
      const event = EventSchema.parse(JSON.parse(line));
      log.events.push(event);
      const cur = log.seqByMission.get(event.missionId) ?? 0;
      log.seqByMission.set(event.missionId, Math.max(cur, event.seq + 1));
    }
    return log;
  }
}
