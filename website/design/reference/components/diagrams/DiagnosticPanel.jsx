import React from "react";

const DEFAULT_ROWS = [
  { group: "envelope" },
  { k: "release", v: "v0.3 · example", t: "s" },
  { k: "target", v: "arm-a / site-1 (conceptual)", t: "s" },
  { group: "model assets" },
  { k: "policy", v: "manipulation-policy · one artifact", t: "n" },
  { k: "action_space", v: "joint velocity · 7 dof", t: "n" },
  { group: "processing" },
  { k: "inputs", v: "wrist camera 30 Hz · joint state 100 Hz", t: "n" },
  { k: "limits", v: "velocity · acceleration · workspace", t: "m" },
  { group: "qualification evidence" },
  { k: "runs", v: "3 recorded · checks pass", t: "s" },
  { k: "sign_off", v: "required before release", t: "m" },
];

export function DiagnosticPanel({ title = "Release contract · conceptual", status = "not an API", rows = DEFAULT_ROWS, footer = "Field names are illustrative. They show what a release must carry, not a schema or command." }) {
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
