/**
 * The pure rule engine: one runtime event in, zero or more notification
 * candidates out (DESIGN §4 classes). No I/O here; routing decides who
 * receives a candidate and the consumer writes it.
 *
 * Class mapping choices, documented once:
 * - `gate_opened` -> checkpoint_opened with the respond deep link; the run
 *   is blocked_on_human and the one correct verb is respond (§5 law 1).
 * - `paused` -> checkpoint_opened with the resume deep link.
 * - `plan_created` -> checkpoint_opened with the approve deep link, but
 *   only when the event itself says approval is wanted: the recorded
 *   payloads carry `run_status` ("awaiting_approval" when the run policy
 *   requires approval, "running" otherwise), and `requires_approval` is
 *   honored if a future payload carries it instead. When neither field is
 *   present the candidate is emitted anyway and routing prefs filter it;
 *   an unwanted notice beats a silently stuck run.
 * - `run_completed` -> run_landed; budget_warning / budget_exhausted /
 *   run_failed map to their own classes.
 * - checkpoint_deadline comes from the open_gates sweep, not the feed.
 * - promotion_requested and improvement_ready have no runtime event; they
 *   are enqueued directly (see consumer.ts) by the promotion flow
 *   (TODO(website-W2)) and the learning packet (TODO(website-W5)).
 *
 * Every cta_url is the TYPED deep link: respond/approve/resume anchor the
 * exact action on the run page; landed and budget notices open the run.
 * There is no generic "resolve".
 */
import { notificationTitles } from "../lexicon";
import type { RunEvent } from "./feed";

export const NOTIFICATION_CLASSES = [
  "checkpoint_opened",
  "checkpoint_deadline",
  "promotion_requested",
  "budget_warning",
  "budget_exhausted",
  "run_failed",
  "run_landed",
  "improvement_ready",
] as const;

export type NotificationClass = (typeof NOTIFICATION_CLASSES)[number];

export interface NotificationCandidate {
  /** Idempotency key; for feed events this is the runtime's `run_id:seq`. */
  eventId: string;
  runId: string;
  notificationClass: NotificationClass;
  title: string;
  ctaUrl: string;
}

function str(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

/** The run goal, when the payload happens to carry it in plain language. */
function goalFrom(payload: Record<string, unknown>): string | undefined {
  const plan = payload.plan as { goal?: unknown } | undefined;
  const report = payload.land_report as { goal?: unknown } | undefined;
  return str(plan?.goal) ?? str(report?.goal);
}

/** Whether a plan_created event signals a plan waiting for approval. */
function planNeedsApproval(payload: Record<string, unknown>): boolean {
  if (typeof payload.requires_approval === "boolean") return payload.requires_approval;
  if (typeof payload.run_status === "string") return payload.run_status === "awaiting_approval";
  // Neither field present: emit and let routing prefs filter (see docstring).
  return true;
}

export function candidatesForEvent(event: RunEvent): NotificationCandidate[] {
  const base = { eventId: event.id, runId: event.run_id };
  const runUrl = `/app/runs/${event.run_id}`;
  switch (event.type) {
    case "gate_opened": {
      const stepId = str(event.payload.step_id) ?? "step";
      return [
        {
          ...base,
          notificationClass: "checkpoint_opened",
          title: notificationTitles.respond(str(event.payload.prompt)),
          ctaUrl: `${runUrl}#respond-${stepId}`,
        },
      ];
    }
    case "paused":
      return [
        {
          ...base,
          notificationClass: "checkpoint_opened",
          title: notificationTitles.resume(),
          ctaUrl: `${runUrl}#resume`,
        },
      ];
    case "plan_created": {
      if (!planNeedsApproval(event.payload)) return [];
      return [
        {
          ...base,
          notificationClass: "checkpoint_opened",
          title: notificationTitles.approve(goalFrom(event.payload)),
          ctaUrl: `${runUrl}#approve-plan`,
        },
      ];
    }
    case "budget_warning":
      return [
        {
          ...base,
          notificationClass: "budget_warning",
          title: notificationTitles.budgetWarning(goalFrom(event.payload)),
          ctaUrl: runUrl,
        },
      ];
    case "budget_exhausted":
      return [
        {
          ...base,
          notificationClass: "budget_exhausted",
          title: notificationTitles.budgetExhausted(goalFrom(event.payload)),
          ctaUrl: runUrl,
        },
      ];
    case "run_failed":
      return [
        {
          ...base,
          notificationClass: "run_failed",
          title: notificationTitles.runFailed(goalFrom(event.payload)),
          ctaUrl: runUrl,
        },
      ];
    case "run_completed":
      return [
        {
          ...base,
          notificationClass: "run_landed",
          title: notificationTitles.runLanded(goalFrom(event.payload)),
          ctaUrl: runUrl,
        },
      ];
    default:
      return [];
  }
}
