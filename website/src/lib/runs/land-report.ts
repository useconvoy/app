/**
 * Pure derivations over the land report (C8): plain facts for the report
 * sections and the stand-in outbox rows for rehearsal runs. Shared by the
 * run detail and the promotion review page; no IO, no server-only imports.
 */
import { copy } from "@/lexicon";

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

/**
 * The stand-in outbox (C8): everything the routine would have done, in
 * plain language. When the runtime's land report carries an explicit
 * `outbox` list it renders verbatim; the scripted runtime's report does
 * not, so rows derive from the run's own records instead. Each completed
 * step with a deliverable becomes a row: side-effecting steps (by their
 * own description) are marked as held by the stand-in, the rest as kept
 * with the run's files, and the deliverable stands in for the content.
 *
 * TODO(runtime): C8 wants the actual email text and the actual record
 * change. The scripted runtime's land report carries neither an outbox
 * list nor deliverable content over the edge; when a real outbox shape
 * lands, render it here and drop the derivation.
 */
export function deriveStandInOutbox(
  report: Record<string, unknown>,
  steps: readonly OutboxStep[],
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
