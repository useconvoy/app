/**
 * Pure display copy for how a routine starts, shared by the routines list
 * and the routine detail header. (Kept out of the action modules:
 * "use server" files may only export async functions.)
 */
import { scheduleDisplay, type AgentSchedule } from "@/lib/agents/schedule";

/**
 * One plain phrase: the schedule when there is one, event triggers when
 * they exist, and "On demand" when the routine only starts by hand.
 * `eventRuleCount` of null means the registry was unreachable and event
 * information is unknown; it is simply omitted rather than shown as zero.
 */
export function startsSummary(
  schedule: AgentSchedule | null,
  eventRuleCount: number | null,
): string {
  const parts: string[] = [];
  if (schedule) parts.push(scheduleDisplay(schedule));
  if (eventRuleCount !== null && eventRuleCount > 0) {
    parts.push(eventRuleCount === 1 ? "1 event trigger" : `${eventRuleCount} event triggers`);
  }
  if (parts.length === 0) return "On demand";
  return parts.join(" · ");
}
