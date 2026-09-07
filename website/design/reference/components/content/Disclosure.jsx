import React from "react";

export function Disclosure({ summary, children, open = false, name }) {
  return (
    <details className="cv-disc" open={open || undefined} name={name}>
      <summary className="cv-disc__summary"><span>{summary}</span><span className="cv-disc__icon" aria-hidden="true" /></summary>
      <div className="cv-disc__body">{children}</div>
    </details>
  );
}
