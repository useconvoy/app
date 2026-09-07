import React from "react";

export function WorkflowSteps({ steps = [] }) {
  return (
    <ol className="cv-steps" style={{ listStyle: "none", margin: 0, padding: 0 }}>
      {steps.map((s, i) => (
        <li className="cv-step" key={s.title}>
          <span className="cv-step__num">{String(i + 1).padStart(2, "0")}{s.tag ? ` · ${s.tag}` : ""}</span>
          <h3 className="cv-step__title">{s.title}</h3>
          <p className="cv-step__body">{s.body}</p>
          {s.items && <ul className="cv-step__list">{s.items.map((it) => <li key={it}>{it}</li>)}</ul>}
        </li>
      ))}
    </ol>
  );
}
