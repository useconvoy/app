import Link from "next/link";
import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: ReactNode;
  /** Header for screen readers only (e.g. a trailing action column). */
  srOnly?: boolean;
  /** Right-aligned tabular numbers. */
  numeric?: boolean;
  /** Hidden on phones, where the table keeps its essential columns. */
  wide?: boolean;
  cell: (row: T) => ReactNode;
}

/**
 * A table of single-line cells. The first column is the row header; with
 * `rowHref` it becomes the row's link and the whole row is clickable, while
 * other links and buttons in the row stay on top.
 */
export function DataTable<T>({ label, columns, rows, rowKey, rowHref, rowClass, empty }: {
  label: string; columns: ReadonlyArray<Column<T>>; rows: readonly T[]; rowKey: (row: T) => string;
  rowHref?: (row: T) => string | null; rowClass?: (row: T) => string | undefined; empty?: ReactNode;
}) {
  const cls = (column: Column<T>) => [column.numeric ? "cv-num" : "", column.wide ? "cv-wide" : ""].filter(Boolean).join(" ") || undefined;
  const [first, ...rest] = columns;
  return <div className="cv-table-wrap">
    <table className="cv-table" aria-label={label}>
      <thead><tr>{columns.map(column => <th key={column.key} scope="col" className={cls(column)}>{column.srOnly ? <span className="cv-sr">{column.header}</span> : column.header}</th>)}</tr></thead>
      <tbody>
        {rows.map(row => {
          const href = rowHref?.(row) ?? null;
          return <tr key={rowKey(row)} className={[href ? "cv-tr-link" : "", rowClass?.(row) ?? ""].filter(Boolean).join(" ") || undefined}>
            <th scope="row" className={cls(first)}>{href ? <Link className="cv-row-link" href={href}>{first.cell(row)}</Link> : first.cell(row)}</th>
            {rest.map(column => <td key={column.key} className={cls(column)}>{column.cell(row)}</td>)}
          </tr>;
        })}
        {!rows.length && <tr><td colSpan={columns.length} className="cv-table__empty">{empty ?? "Nothing yet."}</td></tr>}
      </tbody>
    </table>
  </div>;
}
