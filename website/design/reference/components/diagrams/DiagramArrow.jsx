import React from "react";

/** Diagram connector. kind: execution (solid) | release (dashed) | optional (dotted). */
export function DiagramArrow({ kind = "execution", label }) {
  const dash = kind === "release" ? "6 4" : kind === "optional" ? "2 4" : undefined;
  return (
    <div className={`cv-path__arrow cv-path__arrow--${kind}`} aria-hidden="true" title={label}>
      <svg viewBox="0 0 40 16" fill="none"><path d="M1 8h32" stroke="currentColor" strokeWidth="1.5" strokeDasharray={dash} strokeLinecap="round"/><path d="M29 3.5 34.5 8 29 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
    </div>
  );
}

export function DiagramLegend({ items = ["execution", "release", "optional"] }) {
  const map = { execution: ["solid", "Execution"], release: ["dashed", "Release · configuration · evidence"], optional: ["dotted", "Optional, configuration-dependent placement"] };
  return <div className="cv-dg__legend">{items.map((k) => <span key={k}><i className={map[k][0]} />{map[k][1]}</span>)}</div>;
}
