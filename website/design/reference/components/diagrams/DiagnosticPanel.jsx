import React from "react";

const DEFAULT_ROWS = [
  { group: "release identity" },
  { k: "release", v: "name and configuration reference", t: "m" },
  { group: "model assets" },
  { k: "policy", v: "artifact and preprocessing specification", t: "m" },
  { k: "action_space", v: "units, coordinates, and command form", t: "m" },
  { group: "input and action processing" },
  { k: "inputs", v: "sensor set and alignment", t: "m" },
  { k: "limits", v: "command bounds and translation", t: "m" },
  { group: "runtime and target" },
  { k: "runtime", v: "dependencies and timing budget", t: "m" },
  { k: "target", v: "robot configuration and compute placement", t: "m" },
  { group: "evaluation evidence" },
  { k: "criteria", v: "task and runtime criteria", t: "m" },
  { k: "results", v: "conditions and results for the tested configuration", t: "m" },
];

/** Dark panel listing conceptual field categories of a release. Not used on the launch landing page. Never a terminal or API. */
export function DiagnosticPanel({ title = "Release contents · conceptual categories", status = "not a schema", rows = DEFAULT_ROWS, footer = "Category names are illustrative. They show what a release carries, not an API, schema, or command." }) {
  return (
    <div className="cv-diag">
      <div className="cv-diag__head"><span>{title}</span><span>{status}</span></div>
      <dl className="cv-diag__body" style={{ margin: 0 }}>
        {rows.map((r, i) => r.group ? <div key={i} className="cv-diag__group">{r.group}</div> : (
          <React.Fragment key={i}><dt className="cv-diag__k">{r.k}</dt><dd className={`cv-diag__v ${r.t || ""}`.trim()} style={{ margin: 0 }}>{r.v}</dd></React.Fragment>
        ))}
      </dl>
      {footer && <div className="cv-diag__foot">{footer}</div>}
    </div>
  );
}
