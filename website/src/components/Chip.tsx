import type { ReactNode } from "react";

export type ChipTone = "neutral" | "pass" | "hold" | "fail" | "graphite";

const toneClasses: Record<ChipTone, string> = {
  neutral: "border-line bg-card text-muted",
  pass: "border-pass-soft bg-pass-soft text-pass",
  hold: "border-hold-soft bg-hold-soft text-hold",
  fail: "border-fail-soft bg-fail-soft text-fail",
  graphite: "border-graphite bg-graphite-soft text-graphite",
};

export interface ChipProps {
  tone?: ChipTone;
  /** Dashed border is the rehearsal treatment; never used decoratively. */
  dashed?: boolean;
  /** Mono is reserved for facts from the log: statuses, counts, stamps. */
  mono?: boolean;
  children: ReactNode;
}

export function Chip({ tone = "neutral", dashed = false, mono = false, children }: ChipProps) {
  return (
    <span
      className={[
        "inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs",
        toneClasses[tone],
        dashed ? "border-dashed" : "",
        mono ? "font-mono uppercase tracking-wide" : "",
      ].join(" ")}
    >
      {children}
    </span>
  );
}
