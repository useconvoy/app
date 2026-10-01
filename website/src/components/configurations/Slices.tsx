import Link from "next/link";
import { Fragment } from "react";
import type { ReactNode } from "react";
import { fmtCi, fmtCount, fmtFixed, fmtSeconds, fmtShare, NOT_REPORTED, wilson } from "@/lib/configurations/format";
import type { EvalSuite, SliceResult } from "@/lib/configurations/types";
import { Icon } from "./Icons";

const pct = (share: number) => `${Math.round(Math.min(Math.max(share, 0), 1) * 1000) / 10}%`;

/** Success bar with 95 % CI whisker and dashed gate marker (shares 0–1); decorative, the numbers sit beside it. */
export function Meter({ rate, ci, gate }: { rate: number; ci?: readonly [number, number] | null; gate?: number | null }) {
  return <span className="cfg-meter" aria-hidden="true">
    <span className="cfg-meter__fill" style={{ width: pct(rate) }} />
    {ci && <span className="cfg-meter__ci" style={{ left: pct(ci[0]), width: pct(ci[1] - ci[0]) }} />}
    {gate !== null && gate !== undefined && <span className="cfg-meter__gate" style={{ left: pct(gate) }} />}
  </span>;
}

export interface SliceRowData { result: SliceResult; name: string; weak?: boolean; action?: ReactNode }

/** One slice row: name (flagged when weak), meter, rate, CI, safety per 100, median time, n, action. */
export function SliceRow({ row, gate }: { row: SliceRowData; gate?: number | null }) {
  const { result } = row;
  const rate = result.episodes ? result.successes / result.episodes : null;
  const ci = result.ci95 ?? (result.episodes ? wilson(result.successes, result.episodes) : null);
  return <tr className={row.weak ? "cfg-tr--flag" : undefined}>
    <th scope="row">{row.weak ? <span className="cfg-flagnote"><Icon name="flag" small />{row.name}<span className="cfg-sr"> (weakest slice)</span></span> : row.name}</th>
    <td>{rate !== null && <Meter rate={rate} ci={ci} gate={gate} />}</td>
    <td className="cfg-num">{fmtShare(rate)}</td>
    <td className="cfg-num">{fmtCi(ci, 0)}</td>
    <td className="cfg-num">{result.safetyPer100 === null ? NOT_REPORTED : fmtFixed(result.safetyPer100)}</td>
    <td className="cfg-num">{fmtSeconds(result.medianS)}</td>
    <td className="cfg-num">{fmtCount(result.episodes)}</td>
    <td>{row.action}</td>
  </tr>;
}

/** The ids of the `count` lowest success rates (ties: fewer episodes first). */
export function weakestSlices(results: readonly SliceResult[], count = 2): string[] {
  return results.filter(result => result.episodes > 0).toSorted((a, b) => a.successes / a.episodes - b.successes / b.episodes || a.episodes - b.episodes).slice(0, count).map(result => result.sliceId);
}

/**
 * Scenario-slice matrix grouped by family, with the gate line and the weakest
 * slices flagged. `filterHref` adds a "Filter rollouts" link on weak rows.
 */
export function SliceTable({ suite, results, gate, caption, weak, filterHref }: { suite: EvalSuite; results: readonly SliceResult[]; gate?: number | null; caption: ReactNode; weak?: readonly string[]; filterHref?: (sliceId: string) => string }) {
  const weakest = weak ?? weakestSlices(results);
  const byId = new Map(results.map(result => [result.sliceId, result]));
  return <div className="portal-table-scroll" tabIndex={0} aria-label="Scenario slices, scroll horizontally">
    <table className="portal-table cfg-table">
      <caption>{caption}</caption>
      <thead><tr><th scope="col">Slice</th><th scope="col">Success</th><th scope="col" className="cfg-num">Rate</th><th scope="col" className="cfg-num">95 % CI</th><th scope="col" className="cfg-num">Safety / 100</th><th scope="col" className="cfg-num">Median time</th><th scope="col" className="cfg-num">n</th><th scope="col"><span className="cfg-sr">Action</span></th></tr></thead>
      <tbody>
        {suite.sliceFamilies.map(family => {
          const rows = family.slices.map(slice => ({ slice, result: byId.get(slice.id) })).filter(entry => entry.result);
          if (!rows.length) return null;
          return <Fragment key={family.id}>
            <tr className="cfg-group"><th scope="rowgroup" colSpan={8}>{family.name}</th></tr>
            {rows.map(({ slice, result }) => <SliceRow key={slice.id} gate={gate} row={{
              result: result!, name: slice.name, weak: weakest.includes(slice.id),
              action: weakest.includes(slice.id) && filterHref ? <Link className="portal-table-link" href={filterHref(slice.id)}>Filter rollouts</Link> : null,
            }} />)}
          </Fragment>;
        })}
      </tbody>
    </table>
  </div>;
}
