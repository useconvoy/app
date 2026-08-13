/**
 * Pure schedule vocabulary shared by the form, the actions, and the cards.
 * (Kept out of the action modules: "use server" files may only export
 * async functions.)
 */

export type ScheduleTarget = "rehearsal" | "production";

export interface AgentSchedule {
  cron: string;
  timezone: string;
  target: ScheduleTarget;
  enabled: boolean;
}

/** The form's named presets; "custom" passes the cron through verbatim. */
export const SCHEDULE_PRESETS: Record<string, { cron: string; label: string }> = {
  weekday_morning: { cron: "0 9 * * 1-5", label: "Every weekday at 9:00" },
  daily_morning: { cron: "0 9 * * *", label: "Every day at 9:00" },
  hourly: { cron: "0 * * * *", label: "Every hour" },
  weekly_monday: { cron: "0 9 * * 1", label: "Mondays at 9:00" },
};

const CRON_FIELDS = 5;

export interface ScheduleFormInput {
  preset: string;
  cron?: string;
  timezone: string;
  target: ScheduleTarget;
  enabled: boolean;
}

export function resolveCron(input: ScheduleFormInput): string {
  const preset = SCHEDULE_PRESETS[input.preset];
  const cron = preset ? preset.cron : (input.cron ?? "").trim();
  if (cron.split(/\s+/).length !== CRON_FIELDS) {
    throw new Error("The schedule needs a 5-field cron (minute hour day month weekday)");
  }
  return cron;
}

/** The preset key a stored cron maps back to, or "custom". */
export function presetForCron(cron: string): string {
  const match = Object.entries(SCHEDULE_PRESETS).find(([, entry]) => entry.cron === cron);
  return match ? match[0] : "custom";
}

/** Human copy for the "Starts:" line. */
export function scheduleDisplay(schedule: AgentSchedule): string {
  const preset = Object.values(SCHEDULE_PRESETS).find((entry) => entry.cron === schedule.cron);
  const when = preset ? preset.label : `cron ${schedule.cron}`;
  const zone = schedule.timezone === "UTC" ? "" : ` (${schedule.timezone})`;
  const paused = schedule.enabled ? "" : " (paused)";
  return `${when}${zone}, ${schedule.target}${paused}`;
}

export function parseStoredSchedule(raw: unknown): AgentSchedule | null {
  if (raw == null || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  if (typeof record.cron !== "string" || typeof record.timezone !== "string") return null;
  const target = record.target === "production" ? "production" : "rehearsal";
  return {
    cron: record.cron,
    timezone: record.timezone,
    target,
    enabled: record.enabled !== false,
  };
}
