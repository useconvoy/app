"use client";

import Link from "next/link";
import { useState } from "react";
import type { ReactNode } from "react";
import { Icon } from "./Icons";

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Header text for screen readers only (e.g. a trailing action column). */
  srOnly?: boolean;
  /** Right-aligned tabular numbers (`cfg-num` on the header and every cell). */
  numeric?: boolean;
  /** Let long text wrap (`cfg-wrap`). */
  wrap?: boolean;
  /** Providing a sort value makes the column sortable; null sorts last. */
  sort?: (row: T) => string | number | null;
  /** Direction on the first click (numbers usually "desc"). */
  firstDir?: "asc" | "desc";
  cell: (row: T) => ReactNode;
  /** Secondary line under the value (`<small>`). */
  detail?: (row: T) => ReactNode;
}
export interface SortState { key: string; dir: "asc" | "desc" }

const collator = new Intl.Collator("en-US", { numeric: true, sensitivity: "base" });
function compare(a: string | number | null, b: string | number | null): number {
  if (a === null || b === null) return a === b ? 0 : a === null ? 1 : -1;
  return typeof a === "number" && typeof b === "number" ? a - b : collator.compare(String(a), String(b));
}

/**
 * `portal-table cfg-table` in its focusable scroll wrapper. The first column is the
 * row header; with `rowHref` its content becomes the row link (`cfg-row-link`), which
 * makes the whole row clickable while other links and buttons stay on top.
 * Sorting is internal (from `defaultSort`) unless `sort` + `onSortChange` control it.
 * `pinFirst` keeps rows (e.g. the measured robot) above the sorted rest.
 * `rowCells` can replace every cell after the first for a row (e.g. one colspan cell).
 */
export function DataTable<T>({ caption, label, columns, rows, rowKey, rowHref, rowClass, sort, onSortChange, defaultSort = null, pinFirst, rowCells, empty, className = "" }: {
  caption?: ReactNode; label: string; columns: ReadonlyArray<Column<T>>; rows: readonly T[]; rowKey: (row: T) => string;
  rowHref?: (row: T) => string | null; rowClass?: (row: T) => string | undefined; sort?: SortState | null; onSortChange?: (sort: SortState) => void;
  defaultSort?: SortState | null; pinFirst?: (row: T) => boolean; rowCells?: (row: T) => ReactNode | null; empty?: ReactNode; className?: string;
}) {
  const [internal, setInternal] = useState<SortState | null>(defaultSort);
  const active = sort !== undefined ? sort : internal;
  const column = active ? columns.find(item => item.key === active.key && item.sort) : undefined;
  const ordered = column?.sort ? rows.toSorted((a, b) => {
    const pin = pinFirst ? Number(pinFirst(b)) - Number(pinFirst(a)) : 0;
    return pin || compare(column.sort!(a), column.sort!(b)) * (active!.dir === "asc" ? 1 : -1);
  }) : pinFirst ? rows.toSorted((a, b) => Number(pinFirst(b)) - Number(pinFirst(a))) : rows;
  function choose(item: Column<T>) {
    const next: SortState = active?.key === item.key ? { key: item.key, dir: active.dir === "asc" ? "desc" : "asc" } : { key: item.key, dir: item.firstDir ?? "asc" };
    if (onSortChange) onSortChange(next); else setInternal(next);
  }
  return <div className="portal-table-scroll" tabIndex={0} aria-label={`${label}, scroll horizontally`}>
    <table className={`portal-table cfg-table${className ? ` ${className}` : ""}`}>
      {caption && <caption>{caption}</caption>}
      <thead><tr>{columns.map(item => {
        const sorted = active?.key === item.key && !!item.sort;
        return <th key={item.key} scope="col" className={item.numeric ? "cfg-num" : undefined} aria-sort={sorted ? (active!.dir === "asc" ? "ascending" : "descending") : undefined}>
          {item.sort ? <button className="cfg-sort" type="button" onClick={() => choose(item)}>{item.header} <Icon name={sorted ? (active!.dir === "asc" ? "sort-up" : "sort-down") : "sort"} /></button>
            : item.srOnly ? <span className="cfg-sr">{item.header}</span> : item.header}
        </th>;
      })}</tr></thead>
      <tbody>
        {ordered.map(row => {
          const href = rowHref?.(row) ?? null;
          const override = rowCells?.(row) ?? null;
          const [first, ...rest] = columns;
          return <tr key={rowKey(row)} className={rowClass?.(row) || undefined}>
            <th scope="row" className={first.numeric ? "cfg-num" : first.wrap ? "cfg-wrap" : undefined}>
              {href ? <Link className="portal-table-link cfg-row-link" href={href}>{first.cell(row)}</Link> : first.cell(row)}
              {first.detail && <Detail>{first.detail(row)}</Detail>}
            </th>
            {override ?? rest.map(item => <td key={item.key} className={[item.numeric ? "cfg-num" : "", item.wrap ? "cfg-wrap" : ""].filter(Boolean).join(" ") || undefined}>
              {item.cell(row)}{item.detail && <Detail>{item.detail(row)}</Detail>}
            </td>)}
          </tr>;
        })}
        {!ordered.length && <tr><td colSpan={columns.length} className="cfg-wrap"><p className="portal-empty">{empty ?? "Nothing to show."}</p></td></tr>}
      </tbody>
    </table>
  </div>;
}
function Detail({ children }: { children: ReactNode }) {
  return children === null || children === undefined || children === "" || children === false ? null : <small>{children}</small>;
}
