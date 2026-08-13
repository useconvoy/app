import { describe, expect, it } from "vitest";

import { parseStoredSchedule } from "@/lib/agents/schedule";
import { startsSummary } from "@/lib/routines/summary";

const weekdays = parseStoredSchedule({
  cron: "0 9 * * 1-5",
  timezone: "UTC",
  target: "rehearsal",
  enabled: true,
});

describe("startsSummary", () => {
  it("reads On demand when nothing else starts the routine", () => {
    expect(startsSummary(null, 0)).toBe("On demand");
  });

  it("shows the schedule and the event trigger count together", () => {
    expect(startsSummary(weekdays, 0)).toBe("Every weekday at 9:00, rehearsal");
    expect(startsSummary(weekdays, 2)).toBe(
      "Every weekday at 9:00, rehearsal · 2 event triggers",
    );
    expect(startsSummary(null, 1)).toBe("1 event trigger");
  });

  it("omits event information when the registry was unreachable", () => {
    expect(startsSummary(null, null)).toBe("On demand");
    expect(startsSummary(weekdays, null)).toBe("Every weekday at 9:00, rehearsal");
  });
});
