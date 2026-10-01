import test from "node:test";
import assert from "node:assert/strict";
import { compareSortValues, isMissing, sortRows } from "../../src/lib/configurations/table";

interface Row { id: string; value: number | string | null; pinned?: boolean }
const ids = (rows: readonly Row[]) => rows.map(row => row.id);
const rows: Row[] = [
  { id: "a", value: 3 }, { id: "missing-1", value: null }, { id: "b", value: 10 }, { id: "c", value: 1 }, { id: "missing-2", value: null }, { id: "d", value: 3 },
];

test("missing values sort last in both directions; present values follow the direction", () => {
  assert.deepEqual(ids(sortRows(rows, row => row.value, "asc")), ["c", "a", "d", "b", "missing-1", "missing-2"]);
  assert.deepEqual(ids(sortRows(rows, row => row.value, "desc")), ["b", "a", "d", "c", "missing-1", "missing-2"], "descending keeps missing values last, not first");
});

test("the sort is stable: equal and missing values keep their input order", () => {
  assert.deepEqual(ids(sortRows(rows, () => null, "desc")), ids(rows));
  assert.deepEqual(ids(sortRows([...rows].reverse(), row => row.value, "asc")), ["c", "d", "a", "b", "missing-2", "missing-1"]);
});

test("pinned rows stay first whatever the column and direction", () => {
  const withPin: Row[] = [...rows, { id: "live", value: null, pinned: true }];
  for (const direction of ["asc", "desc"] as const) {
    assert.equal(ids(sortRows(withPin, row => row.value, direction, row => !!row.pinned))[0], "live", direction);
  }
  assert.deepEqual(ids(sortRows(withPin, row => row.value, "desc", row => !!row.pinned)).slice(-2), ["missing-1", "missing-2"]);
});

test("text compares in natural order; NaN and undefined count as missing", () => {
  const names: Row[] = [{ id: "u10", value: "Unit 10" }, { id: "u2", value: "Unit 2" }, { id: "none", value: null }, { id: "u1", value: "unit 1" }];
  assert.deepEqual(ids(sortRows(names, row => row.value, "asc")), ["u1", "u2", "u10", "none"]);
  assert.deepEqual(ids(sortRows(names, row => row.value, "desc")), ["u10", "u2", "u1", "none"]);
  assert.ok(isMissing(Number.NaN) && isMissing(undefined) && isMissing(null) && !isMissing(0) && !isMissing(""));
  assert.equal(compareSortValues(Number.NaN, 0, "desc"), 1);
  assert.equal(compareSortValues(0, undefined, "asc"), -1);
  assert.equal(compareSortValues(null, undefined, "asc"), 0);
});
