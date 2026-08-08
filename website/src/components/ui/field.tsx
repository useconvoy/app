import type { ReactNode } from "react";

/**
 * The form vocabulary: one label treatment, one hint treatment, one error
 * treatment, composed around any control.
 *
 * Labels are the console's small-caps mono, the same voice as column
 * headers and status chips, so a form reads as part of the instrument
 * rather than a web page embedded in it. Hint and error share a slot and a
 * size; when an error arrives it replaces the hint instead of pushing the
 * form taller.
 */
export function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string;
  hint?: string;
  /** Replaces the hint; announced, colored, same size so nothing reflows. */
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <label className="block min-w-0">
      <span className="mb-1.5 block font-mono text-[11px] font-semibold tracking-[0.09em] text-muted uppercase">
        {label}
      </span>
      {children}
      {error ? (
        <span role="alert" className="mt-1.5 block text-xs leading-snug text-fail">
          {error}
        </span>
      ) : hint ? (
        <span className="mt-1.5 block text-xs leading-snug text-muted">{hint}</span>
      ) : null}
    </label>
  );
}

/**
 * The shared skin for boxy controls: text, number, select, textarea. One
 * place decides the border, the fill, the focus treatment, and the type
 * size, so every box on every form matches.
 */
export const CONTROL =
  "w-full rounded-md border border-line bg-card px-3 py-2 text-sm text-ink " +
  "placeholder:text-muted/70 " +
  "focus:outline-2 focus:outline-offset-[-1px] focus:outline-pass " +
  "disabled:cursor-not-allowed disabled:opacity-50";
