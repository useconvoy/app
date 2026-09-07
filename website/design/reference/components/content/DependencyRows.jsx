import React from "react";

/** Aligned rows: term | one or more description cells. Rows may use `body` (single column) or `model`/`deployment` (two columns). */
export function DependencyRows({ columns, rows = [] }) {
  const twoCol = rows.some((r) => r.deployment !== undefined);
  const cols = columns || (twoCol ? ["", "What the model gives you", "What a robot deployment needs"] : ["", ""]);
  const showHead = cols.slice(1).some(Boolean);
  const cls = twoCol ? "cv-dep cv-dep--2" : "cv-dep cv-dep--1";
  return (
    <div className={cls} role="table">
      {showHead && <div className="cv-dep__head" role="row">{cols.map((c, i) => <div key={i} role="columnheader">{c}</div>)}</div>}
      {rows.map((r, i) => (
        <div className="cv-dep__row" role="row" key={r.term}>
          <div className="cv-dep__term" role="rowheader"><span className="cv-dep__idx">{String(i + 1).padStart(2, "0")}</span>{r.term}</div>
          {twoCol ? (
            <>
              <div className="cv-dep__cell" role="cell">{cols[1] && <span className="cv-dep__cell-label">{cols[1]}</span>}{r.model}</div>
              <div className="cv-dep__cell" role="cell">{cols[2] && <span className="cv-dep__cell-label">{cols[2]}</span>}{r.deployment}</div>
            </>
          ) : (
            <div className="cv-dep__cell" role="cell">{r.body}</div>
          )}
        </div>
      ))}
    </div>
  );
}
