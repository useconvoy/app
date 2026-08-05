/**
 * Formatting for facts from the log: friendly dates, money, and ages.
 * All user-facing timestamps and amounts render through here; raw ISO
 * strings appear only as hover/copy affordances in detail views.
 */

const MONTHS = [
  "JAN",
  "FEB",
  "MAR",
  "APR",
  "MAY",
  "JUN",
  "JUL",
  "AUG",
  "SEP",
  "OCT",
  "NOV",
  "DEC",
] as const;

/** Friendly timestamp, e.g. "AUG 3 · 9:12AM". */
export function friendlyDateTime(value: Date | string): string {
  const date = typeof value === "string" ? new Date(value) : value;
  const hours24 = date.getHours();
  const hours12 = hours24 % 12 === 0 ? 12 : hours24 % 12;
  const minutes = String(date.getMinutes()).padStart(2, "0");
  const meridiem = hours24 < 12 ? "AM" : "PM";
  return `${MONTHS[date.getMonth()]} ${date.getDate()} · ${hours12}:${minutes}${meridiem}`;
}

/** Friendly date without time, e.g. "AUG 3". */
export function friendlyDate(value: Date | string): string {
  const date = typeof value === "string" ? new Date(value) : value;
  return `${MONTHS[date.getMonth()]} ${date.getDate()}`;
}

/** Deadline stamp for checkpoint cards, e.g. "DUE 08-05". */
export function dueDateLabel(value: Date | string): string {
  const date = typeof value === "string" ? new Date(value) : value;
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `DUE ${month}-${day}`;
}

/**
 * Money for display. Whole-dollar amounts drop cents ("$75"); fractional
 * amounts keep two places ("$12.40"). Values arrive as the API's decimal
 * strings or as numbers.
 */
export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "$0";
  const amount = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(amount)) return "$0";
  const isWhole = Math.abs(amount - Math.round(amount)) < 0.005;
  return isWhole ? `$${Math.round(amount)}` : `$${amount.toFixed(2)}`;
}

/** Relative age in compact units, e.g. "26m", "3h", "2d". */
export function age(from: Date | string, to: Date = new Date()): string {
  const start = typeof from === "string" ? new Date(from) : from;
  const minutes = Math.max(Math.floor((to.getTime() - start.getTime()) / 60_000), 0);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  return `${Math.floor(hours / 24)}d`;
}

/** Minutes elapsed since a timestamp, for held-age labels. */
export function minutesSince(from: Date | string, to: Date = new Date()): number {
  const start = typeof from === "string" ? new Date(from) : from;
  return Math.max(Math.floor((to.getTime() - start.getTime()) / 60_000), 0);
}
