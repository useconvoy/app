import React from "react";

/** Group of CTA actions. Inline on desktop, stacked full-width ≤640px. Buttons for actions, links for destinations. */
export function ActionGroup({ children, stack = false, note, className = "" }) {
  return (
    <div className={`cv-actions${stack ? " cv-actions--stack" : ""} ${className}`.trim()}>
      {children}
      {note && <p className="cv-actions__note">{note}</p>}
    </div>
  );
}
