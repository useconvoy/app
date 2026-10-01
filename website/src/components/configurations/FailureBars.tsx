import { fmtCount } from "@/lib/configurations/format";

/**
 * Failure modes as horizontal bars from a shared baseline, sorted by count and
 * scaled to the largest; each value reads "29 · 37 %" of `total` failed episodes.
 * One series in signal green: this is not status.
 */
export function FailureBars({ items, total, label }: { items: ReadonlyArray<{ label: string; count: number }>; total: number; label: string }) {
  const sorted = items.toSorted((a, b) => b.count - a.count);
  const max = Math.max(1, ...sorted.map(item => item.count));
  if (!sorted.length) return <p className="portal-empty">No failures recorded.</p>;
  return <ul className="cfg-hbars" aria-label={label}>
    {sorted.map(item => <li className="cfg-hbar" key={item.label}>
      <span>{item.label}</span>
      <span className="cfg-hbar__track" aria-hidden="true"><span className="cfg-hbar__fill" style={{ width: `${Math.round(item.count / max * 1000) / 10}%` }} /></span>
      <span className="cfg-hbar__value">{fmtCount(item.count)}{total > 0 && ` · ${Math.round(item.count / total * 100)} %`}</span>
    </li>)}
  </ul>;
}
