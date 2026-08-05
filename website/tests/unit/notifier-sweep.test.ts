/**
 * Deadline sweep window math: inclusion inside the window, the exact
 * boundary, overdue-but-open gates, and the skips (no deadline, already
 * notified).
 */
import { describe, expect, it } from "vitest";

import {
  DEFAULT_DEADLINE_WINDOW_MS,
  deadlineEventId,
  gatesDueForNotice,
  type OpenGate,
} from "@/notifier/sweep";

const NOW = new Date("2026-08-05T12:00:00Z");
const MINUTE = 60 * 1000;

function gate(overrides: Partial<OpenGate>): OpenGate {
  return { runId: "run-1", stepId: "step-1", deadline: null, notifiedAt: null, ...overrides };
}

describe("deadline sweep math", () => {
  it("defaults to a 60 minute warning window", () => {
    expect(DEFAULT_DEADLINE_WINDOW_MS).toBe(60 * MINUTE);
  });

  it("notices a deadline inside the window", () => {
    const gates = [gate({ deadline: new Date(NOW.getTime() + 30 * MINUTE) })];
    expect(gatesDueForNotice(gates, NOW)).toHaveLength(1);
  });

  it("includes the exact window boundary", () => {
    const gates = [gate({ deadline: new Date(NOW.getTime() + 60 * MINUTE) })];
    expect(gatesDueForNotice(gates, NOW)).toHaveLength(1);
  });

  it("skips a deadline beyond the window", () => {
    const gates = [gate({ deadline: new Date(NOW.getTime() + 60 * MINUTE + 1) })];
    expect(gatesDueForNotice(gates, NOW)).toHaveLength(0);
  });

  it("still notices an overdue gate that nobody answered", () => {
    const gates = [gate({ deadline: new Date(NOW.getTime() - 10 * MINUTE) })];
    expect(gatesDueForNotice(gates, NOW)).toHaveLength(1);
  });

  it("skips gates with no deadline", () => {
    expect(gatesDueForNotice([gate({})], NOW)).toHaveLength(0);
  });

  it("skips gates already noticed", () => {
    const gates = [
      gate({
        deadline: new Date(NOW.getTime() + 30 * MINUTE),
        notifiedAt: new Date(NOW.getTime() - 5 * MINUTE),
      }),
    ];
    expect(gatesDueForNotice(gates, NOW)).toHaveLength(0);
  });

  it("respects a custom window", () => {
    const gates = [gate({ deadline: new Date(NOW.getTime() + 30 * MINUTE) })];
    expect(gatesDueForNotice(gates, NOW, 15 * MINUTE)).toHaveLength(0);
    expect(gatesDueForNotice(gates, NOW, 30 * MINUTE)).toHaveLength(1);
  });

  it("derives a deterministic one-shot event id per gate", () => {
    expect(deadlineEventId("run-9", "step-2")).toBe("run-9:step-2:deadline");
    expect(deadlineEventId("run-9", "step-2")).toBe(deadlineEventId("run-9", "step-2"));
  });
});
