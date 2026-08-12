/**
 * Pure schedule vocabulary: preset resolution, cron validation, display
 * copy, and stored-shape parsing.
 */
import { describe, expect, it } from "vitest";

import {
  SCHEDULE_PRESETS,
  parseStoredSchedule,
  presetForCron,
  resolveCron,
  scheduleDisplay,
} from "@/lib/agents/schedule";

describe("resolveCron", () => {
  it("maps presets to their cron", () => {
    expect(
      resolveCron({ preset: "weekday_morning", timezone: "UTC", target: "rehearsal", enabled: true }),
    ).toBe("0 9 * * 1-5");
  });

  it("passes a custom 5-field cron through", () => {
    expect(
      resolveCron({
        preset: "custom",
        cron: "30 7 * * 2",
        timezone: "UTC",
        target: "rehearsal",
        enabled: true,
      }),
    ).toBe("30 7 * * 2");
  });

  it("rejects a cron without 5 fields", () => {
    expect(() =>
      resolveCron({
        preset: "custom",
        cron: "every morning",
        timezone: "UTC",
        target: "rehearsal",
        enabled: true,
      }),
    ).toThrow(/5-field/);
  });
});

describe("presetForCron / scheduleDisplay", () => {
  it("round-trips every preset", () => {
    for (const [key, entry] of Object.entries(SCHEDULE_PRESETS)) {
      expect(presetForCron(entry.cron)).toBe(key);
    }
    expect(presetForCron("1 2 3 4 5")).toBe("custom");
  });

  it("writes the Starts line a human can read", () => {
    expect(
      scheduleDisplay({ cron: "0 9 * * 1-5", timezone: "UTC", target: "rehearsal", enabled: true }),
    ).toBe("Every weekday at 9:00, rehearsal");
    expect(
      scheduleDisplay({
        cron: "0 9 * * 1-5",
        timezone: "America/Chicago",
        target: "production",
        enabled: false,
      }),
    ).toBe("Every weekday at 9:00 (America/Chicago), production (paused)");
  });
});

describe("parseStoredSchedule", () => {
  it("parses the stored jsonb shape", () => {
    expect(
      parseStoredSchedule({ cron: "0 * * * *", timezone: "UTC", target: "production" }),
    ).toEqual({ cron: "0 * * * *", timezone: "UTC", target: "production", enabled: true });
  });

  it("returns null for junk", () => {
    expect(parseStoredSchedule(null)).toBeNull();
    expect(parseStoredSchedule("cron")).toBeNull();
    expect(parseStoredSchedule({ timezone: "UTC" })).toBeNull();
  });
});
