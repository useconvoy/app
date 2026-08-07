import { runStatusLabels, terms } from "@/lexicon";

type Tone = "neutral" | "pass" | "hold" | "fail";

/** Status colors are semantic, never decorative. */
const toneByStatus: Record<string, Tone> = {
  planning: "neutral",
  awaiting_approval: "hold",
  running: "pass",
  paused: "hold",
  blocked_on_human: "hold",
  landing: "pass",
  landed: "pass",
  completed: "pass",
  failed: "fail",
  budget_exhausted: "fail",
};

const toneClasses: Record<Tone, string> = {
  neutral: "border-line bg-card text-muted",
  pass: "border-pass-soft bg-pass-soft text-pass-text",
  hold: "border-hold-soft bg-hold-soft text-hold-text",
  fail: "border-fail-soft bg-fail-soft text-fail",
};

export interface StatusChipProps {
  /** Runtime status value; the label always renders through the lexicon. */
  status: string;
  /** Rehearsal context gets the pencil treatment: graphite, dashed, labeled. */
  rehearsal?: boolean;
}

export function StatusChip({ status, rehearsal = false }: StatusChipProps) {
  const label = runStatusLabels[status] ?? status.replaceAll("_", " ");
  const tone = toneByStatus[status] ?? "neutral";
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5",
        "font-mono text-xs uppercase tracking-wide",
        rehearsal ? "border-dashed border-graphite bg-graphite-soft text-graphite" : toneClasses[tone],
      ].join(" ")}
    >
      {status === "running" && (
        <span aria-hidden="true" className="pulse-live size-1.5 rounded-full bg-pass" />
      )}
      <span>{label}</span>
      {rehearsal && (
        <>
          <span aria-hidden="true">·</span>
          <span>{terms.sandbox}</span>
        </>
      )}
    </span>
  );
}
