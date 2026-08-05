/**
 * Rule engine over the RECORDED fixtures: every file replays through
 * candidatesForEvent and the emitted classes, deep-link shapes, and copy
 * rules are asserted. No invented events.
 */
import { describe, expect, it } from "vitest";

import type { RunEvent } from "@/notifier/feed";
import { candidatesForEvent, type NotificationCandidate } from "@/notifier/rules";

import approved from "../fixtures/sse/run-approved.json";
import gated from "../fixtures/sse/run-gated.json";
import landed from "../fixtures/sse/run-landed.json";
import pausedResumed from "../fixtures/sse/run-paused-resumed.json";
import rehearsalVirtual from "../fixtures/sse/run-rehearsal-virtual.json";

const fixtures: Record<string, RunEvent[]> = {
  "run-approved": approved as unknown as RunEvent[],
  "run-gated": gated as unknown as RunEvent[],
  "run-landed": landed as unknown as RunEvent[],
  "run-paused-resumed": pausedResumed as unknown as RunEvent[],
  "run-rehearsal-virtual": rehearsalVirtual as unknown as RunEvent[],
};

function replay(events: RunEvent[]): NotificationCandidate[] {
  return events.flatMap((event) => candidatesForEvent(event));
}

describe("notifier rules over recorded fixtures", () => {
  it("run-gated: a question checkpoint, then the landing", () => {
    const candidates = replay(fixtures["run-gated"]!);
    expect(candidates.map((c) => c.notificationClass)).toEqual([
      "checkpoint_opened",
      "run_landed",
    ]);
    // plan_created with run_status "running" must NOT raise an approval item.
    const planEvent = fixtures["run-gated"]!.find((e) => e.type === "plan_created")!;
    expect(candidatesForEvent(planEvent)).toEqual([]);
  });

  it("run-approved: plan approval checkpoint, then the landing", () => {
    const candidates = replay(fixtures["run-approved"]!);
    expect(candidates.map((c) => c.notificationClass)).toEqual([
      "checkpoint_opened",
      "run_landed",
    ]);
    const approvalItem = candidates[0]!;
    expect(approvalItem.ctaUrl).toMatch(/^\/app\/runs\/[\w-]+#approve-plan$/);
  });

  it("run-paused-resumed: a resume checkpoint, then the landing", () => {
    const candidates = replay(fixtures["run-paused-resumed"]!);
    expect(candidates.map((c) => c.notificationClass)).toEqual([
      "checkpoint_opened",
      "run_landed",
    ]);
    expect(candidates[0]!.ctaUrl).toMatch(/^\/app\/runs\/[\w-]+#resume$/);
  });

  it("run-landed: only the landing", () => {
    const candidates = replay(fixtures["run-landed"]!);
    expect(candidates.map((c) => c.notificationClass)).toEqual(["run_landed"]);
  });

  it("run-rehearsal-virtual: the gate question, then the landing", () => {
    const candidates = replay(fixtures["run-rehearsal-virtual"]!);
    expect(candidates.map((c) => c.notificationClass)).toEqual([
      "checkpoint_opened",
      "run_landed",
    ]);
  });

  it("respond deep links anchor the exact step; landings open the run", () => {
    const events = fixtures["run-gated"]!;
    const gateEvent = events.find((e) => e.type === "gate_opened")!;
    const [respond] = candidatesForEvent(gateEvent);
    expect(respond!.ctaUrl).toBe(`/app/runs/${gateEvent.run_id}#respond-step-2`);
    // The respond title is the checkpoint's own plain prompt.
    expect(respond!.title).toBe("Look over the exception memos before they go out");

    const landedEvent = events.find((e) => e.type === "run_completed")!;
    const [landing] = candidatesForEvent(landedEvent);
    expect(landing!.ctaUrl).toBe(`/app/runs/${landedEvent.run_id}`);
    expect(landing!.title).toContain("Write exception memos and hold them for review");
  });

  it("idempotency key is the runtime's gapless event id (run_id:seq)", () => {
    for (const events of Object.values(fixtures)) {
      for (const event of events) {
        for (const candidate of candidatesForEvent(event)) {
          expect(candidate.eventId).toBe(event.id);
          expect(candidate.eventId).toBe(`${event.run_id}:${event.seq}`);
          expect(candidate.runId).toBe(event.run_id);
        }
      }
    }
  });

  it("titles carry no internal nouns and no em dashes", () => {
    const internalNoun =
      /(?<![\w-])(agent|agents|sandbox|sandboxes|eval|evals|artifact|artifacts|gate|gates|tenant|tenants|connector|connectors|telemetry)(?![\w-])/i;
    for (const events of Object.values(fixtures)) {
      for (const candidate of replay(events)) {
        expect(candidate.title).not.toMatch(internalNoun);
        expect(candidate.title).not.toContain("—");
      }
    }
  });
});
