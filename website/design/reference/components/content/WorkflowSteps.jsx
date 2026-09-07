import React from "react";
import { Disclosure } from "./Disclosure.jsx";

/** Numbered workflow sequence (Package / Qualify / Release). Three columns ≥900px, ordered stack below. Optional per-step disclosure for secondary detail. */
export function WorkflowSteps({ steps = [] }) {
  return (
    <ol className="cv-steps">
      {steps.map((s, i) => (
        <li className="cv-step" key={s.title}>
          <span className="cv-step__num">{String(i + 1).padStart(2, "0")}{s.tag ? ` · ${s.tag}` : ""}</span>
          <h3 className="cv-step__title">{s.title}</h3>
          <p className="cv-step__body">{s.body}</p>
          {s.items && <ul className="cv-step__list">{s.items.map((it) => <li key={it}>{it}</li>)}</ul>}
          {s.detail && <Disclosure summary={s.detail.summary} compact>{s.detail.body}</Disclosure>}
        </li>
      ))}
    </ol>
  );
}
export const WorkflowSequence = WorkflowSteps;
