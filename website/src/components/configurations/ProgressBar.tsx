/** Episode progress: information-blue fill on a lighter track. `wide` is the full-width 6px variant. */
export function ProgressBar({ done, total, label, wide = false }: { done: number; total: number; label: string; wide?: boolean }) {
  const share = total > 0 ? Math.min(Math.max(done / total, 0), 1) : 0;
  return <span className={`cfg-progress${wide ? " cfg-progress--wide" : ""}`} role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}>
    <span className="cfg-progress__bar" style={{ width: `${Math.round(share * 1000) / 10}%` }} />
  </span>;
}
