/**
 * Sorting for the Configurations tables (`DataTable`): one comparison that keeps
 * missing values last in either direction, and a stable sort with pinned rows.
 * Pure functions; the component lives in src/components/configurations/DataTable.tsx.
 */

export type SortValue = string | number | null | undefined;
export type SortDirection = "asc" | "desc";

const collator = new Intl.Collator("en-US", { numeric: true, sensitivity: "base" });
/** A value with nothing to sort by: null, undefined or NaN ("Not reported" in a cell). */
export const isMissing = (value: SortValue): value is null | undefined => value === null || value === undefined || (typeof value === "number" && Number.isNaN(value));

/**
 * Compares two column values in a direction. Missing values always sort after
 * present ones, ascending or descending; numbers compare numerically, anything
 * else as text in natural order ("Unit 2" before "Unit 10").
 */
export function compareSortValues(a: SortValue, b: SortValue, direction: SortDirection): number {
  const missingA = isMissing(a), missingB = isMissing(b);
  if (missingA || missingB) return missingA === missingB ? 0 : missingA ? 1 : -1;
  const order = typeof a === "number" && typeof b === "number" ? a - b : collator.compare(String(a), String(b));
  return direction === "asc" ? order : -order;
}

/**
 * Rows sorted by `value` in `direction`, with rows matching `pin` kept first.
 * Stable: rows that compare equal keep their input order. Missing values last.
 */
export function sortRows<T>(rows: readonly T[], value: (row: T) => SortValue, direction: SortDirection, pin?: (row: T) => boolean): T[] {
  return rows.toSorted((a, b) => (pin ? Number(pin(b)) - Number(pin(a)) : 0) || compareSortValues(value(a), value(b), direction));
}
