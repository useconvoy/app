/**
 * Pure derivations over the land report: plain facts for the report
 * sections and the stand-in outbox rows for rehearsal runs. Shared by the
 * run detail and the promotion review page; no IO, no server-only imports.
 */
import { copy } from "@/lexicon";
import type { RunStreamEvent } from "@/lib/runs/status";

export interface LandReportFacts {
  goal: string;
  status: string;
  costUsd: string | null;
  stepsDone: number;
  stepsFailed: number;
  stepsSkipped: number;
  files: Array<{ name: string; sizeBytes: number | null }>;
}

function num(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function baseName(key: unknown): string | null {
  if (typeof key !== "string" || key.length === 0) return null;
  return key.split("/").pop() ?? key;
}

/** The report's facts, read defensively; shapes come from the runtime. */
export function landReportFacts(report: Record<string, unknown>): LandReportFacts {
  const deliverables = Array.isArray(report["deliverables"])
    ? (report["deliverables"] as Array<Record<string, unknown>>)
    : [];
  return {
    goal: typeof report["goal"] === "string" ? (report["goal"] as string) : "",
    status: typeof report["status"] === "string" ? (report["status"] as string) : "",
    costUsd: typeof report["cost_usd"] === "string" ? (report["cost_usd"] as string) : null,
    stepsDone: num(report["steps_done"]),
    stepsFailed: num(report["steps_failed"]),
    stepsSkipped: num(report["steps_skipped"]),
    files: deliverables
      .map((ref) => ({
        name: baseName(ref["key"]),
        sizeBytes: typeof ref["size_bytes"] === "number" ? (ref["size_bytes"] as number) : null,
      }))
      .filter((file): file is { name: string; sizeBytes: number | null } => file.name !== null),
  };
}

export interface OutboxRow {
  /** What the routine would have done, as a plain sentence. */
  what: string;
  /** To whom or where it would have gone. */
  where: string;
  /** The actual content where available, else what stands in for it. */
  content: string;
}

export interface OutboxStep {
  step_id: string;
  description?: string | null;
  status: string;
}

/** Verbs that read as touching the world outside the organization. */
const SIDE_EFFECT_PATTERN =
  /\b(send|sends|sent|email|emails|remind|reminds|chase|chases|request|requests|notify|notifies|message|messages|post|posts|update|updates|file|files)\b/i;

function simulatedEffectRows(events: readonly RunStreamEvent[]): OutboxRow[] {
  const seen = new Set<string>();
  const rows: OutboxRow[] = [];
  for (const event of events) {
    if (event.type !== "simulated_effect") continue;
    const toolId = event.payload["tool_id"];
    if (typeof toolId !== "string" || toolId.length === 0) continue;
    const key = event.payload["idempotency_key"];
    const dedupeKey = typeof key === "string" && key.length > 0 ? key : event.id;
    if (seen.has(dedupeKey)) continue;
    seen.add(dedupeKey);

    const [provider = "System", ...actionParts] = toolId.split(".");
    const action = actionParts.join(" ").replaceAll("_", " ");
    const result = event.payload["result"];
    const record = result && typeof result === "object" ? (result as Record<string, unknown>) : null;
    const message =
      record?.["message"] && typeof record["message"] === "object"
        ? (record["message"] as Record<string, unknown>)
        : null;
    const messageText = typeof message?.["text"] === "string" ? message["text"] : null;
    const channel = typeof record?.["channel"] === "string" ? record["channel"] : null;
    let serialized = "";
    try {
      serialized = JSON.stringify(result);
    } catch {
      serialized = String(result ?? "");
    }
    rows.push({
      what: `${provider.charAt(0).toUpperCase()}${provider.slice(1)}: ${action || "record effect"}`,
      where: channel || copy.outboxOutsideWorld,
      content: messageText || serialized || copy.outboxContentHeld,
    });
  }
  return rows;
}

/**
 * The stand-in outbox: everything the routine would have done, in
 * plain language. When the runtime's land report carries an explicit
 * `outbox` list it renders verbatim. Otherwise recorded `simulated_effect`
 * events are the source of truth. Older runtime histories have neither, so
 * rows derive from the run's own records instead. Each completed
 * step with a deliverable becomes a row: side-effecting steps (by their
 * own description) are marked as held by the stand-in, the rest as kept
 * with the run's files, and the deliverable stands in for the content.
 *
 * The final derivation is deliberately a compatibility fallback and can be
 * removed once all retained run histories carry explicit effect records.
 */
export function deriveStandInOutbox(
  report: Record<string, unknown>,
  steps: readonly OutboxStep[],
  events: readonly RunStreamEvent[] = [],
): OutboxRow[] {
  const explicit = report["outbox"];
  if (Array.isArray(explicit)) {
    return (explicit as Array<Record<string, unknown>>).map((row) => ({
      what: typeof row["what"] === "string" ? (row["what"] as string) : "",
      where:
        typeof row["to"] === "string"
          ? (row["to"] as string)
          : typeof row["where"] === "string"
            ? (row["where"] as string)
            : "",
      content: typeof row["content"] === "string" ? (row["content"] as string) : "",
    }));
  }

  const effects = simulatedEffectRows(events);
  if (effects.length > 0) return effects;

  const deliverables = Array.isArray(report["deliverables"])
    ? (report["deliverables"] as Array<Record<string, unknown>>)
    : [];
  const fileByStep = new Map<string, string>();
  for (const ref of deliverables) {
    const name = baseName(ref["key"]);
    if (!name) continue;
    // Output keys carry the step id as the file stem, e.g. "step-2.json".
    fileByStep.set(name.replace(/\.[^.]+$/, ""), name);
  }

  return steps
    .filter((step) => step.status === "done" && (step.description ?? "").length > 0)
    .map((step) => {
      const description = step.description ?? "";
      const file = fileByStep.get(step.step_id) ?? null;
      const outward = SIDE_EFFECT_PATTERN.test(description);
      return {
        what: description,
        where: outward ? copy.outboxOutsideWorld : copy.outboxKeptWithFiles,
        content: file ?? copy.outboxContentHeld,
      };
    });
}
