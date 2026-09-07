import React from "react";

/** Page shell: skip link target wiring, sticky header slot, main, footer. */
export function PageShell({ header, footer, children, mainId = "main" }) {
  return (
    <div className="cv-page">
      {header}
      <main id={mainId} tabIndex={-1} style={{ outline: "none" }}>{children}</main>
      {footer}
    </div>
  );
}

/** Section container: max-width, page padding, vertical section gap, optional top rule, optional 5/7 grid. */
export function Section({ id, labelledBy, rule = true, grid, className = "", children, as = "section" }) {
  const Tag = as;
  const cls = ["cv-section", rule ? "cv-section--rule" : "", className].filter(Boolean).join(" ");
  const inner = grid ? <div className={`cv-section__grid${grid === "wide-left" ? " cv-section__grid--wide-left" : grid === "even" ? " cv-section__grid--even" : ""}`}>{children}</div> : children;
  return <Tag id={id} className={cls} aria-labelledby={labelledBy}><div className="cv-container">{inner}</div></Tag>;
}
