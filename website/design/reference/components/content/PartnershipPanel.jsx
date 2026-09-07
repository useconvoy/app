import React from "react";
import { SectionHeading } from "./SectionHeading.jsx";

/** Design-partnership panel: heading + lead + CTA on the left, fit/not-yet list on the right; single column ≤900px. */
export function PartnershipPanel({ eyebrow = "Design partnership", title, lead, id, actions, fit = [] }) {
  return (
    <div className="cv-panel cv-panel--grid">
      <div>
        <SectionHeading id={id} eyebrow={eyebrow} title={title} lead={lead} />
        {actions && <div className="cv-panel__actions">{actions}</div>}
      </div>
      {fit.length > 0 && (
        <ul className="cv-fit">{fit.map((f) => <li key={f.body}><span className="cv-eyebrow">{f.label}</span><p>{f.body}</p></li>)}</ul>
      )}
    </div>
  );
}
