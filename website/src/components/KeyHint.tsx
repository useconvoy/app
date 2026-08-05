export interface KeyHintItem {
  /** The key cap, e.g. "A". */
  keyName: string;
  /** What pressing it does, e.g. "approve". */
  label: string;
}

export interface KeyHintProps {
  hints: KeyHintItem[];
}

/** Key-cap styled keyboard hints, e.g. "A approve · E edit · R reject". */
export function KeyHint({ hints }: KeyHintProps) {
  return (
    <span className="inline-flex items-center gap-2 text-xs text-muted">
      {hints.map((hint, index) => (
        <span key={hint.keyName} className="inline-flex items-center gap-1">
          {index > 0 && <span aria-hidden="true">·</span>}
          <kbd className="rounded border border-line bg-card px-1 font-mono text-[11px] text-ink">
            {hint.keyName}
          </kbd>
          <span>{hint.label}</span>
        </span>
      ))}
    </span>
  );
}
