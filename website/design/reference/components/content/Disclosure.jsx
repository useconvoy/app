import React from "react";

/** Native details/summary. Multiple may stay open (omit `name`). Enter/Space toggle, expanded state exposed by the element itself. */
export function Disclosure({ summary, children, open = false, name, compact = false }) {
  return (
    <details className={`cv-disc${compact ? " cv-disc--compact" : ""}`} open={open || undefined} name={name}>
      <summary className="cv-disc__summary"><span>{summary}</span><span className="cv-disc__icon" aria-hidden="true" /></summary>
      <div className="cv-disc__body">{children}</div>
    </details>
  );
}
