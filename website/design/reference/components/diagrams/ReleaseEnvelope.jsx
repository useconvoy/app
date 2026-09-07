import React from "react";
import { DiagramLegend } from "./DiagramArrow.jsx";

const DEFAULT_GROUPS = [
  { key: "model", title: "Model assets", note: "what was trained", items: ["policy weights", "preprocessing spec", "action space"] },
  { key: "processing", title: "Input and action processing", note: "how signals are shaped", items: ["sensor sync", "normalisation", "command limits"] },
  { key: "runtime", title: "Runtime", note: "what executes it", items: ["inference loop", "timing budget", "health checks"] },
  { key: "target", title: "Target configuration", note: "where it runs", items: ["robot config", "compute placement", "controller interface"] },
  { key: "evidence", title: "Qualification evidence", note: "why it may be released", items: ["test runs", "checks passed", "sign-off"], evidence: true, wide: true },
];

export function ReleaseEnvelope({ version = "release v0.3", label = "example", groups = DEFAULT_GROUPS, legend = true, caption = "Labels are conceptual examples. Robot controller and safety remain outside Convoy ownership." }) {
  return (
    <figure className="cv-dg" style={{ margin: 0 }}>
      <div className="cv-env">
        <div className="cv-env__tag"><span>{version}</span>{label && <em>· {label}</em>}</div>
        <div className="cv-env__grid">
          {groups.map((g) => (
            <div key={g.key || g.title} className={["cv-env__group", g.wide ? "cv-env__group--wide" : "", g.evidence ? "cv-env__group--evidence" : ""].filter(Boolean).join(" ")}>
              <h4>{g.title}{g.note && <small>{g.note}</small>}</h4>
              {g.items && <div className="cv-env__items">{g.items.map((it) => <span key={it} className={`cv-env__item${g.evidence ? " cv-env__item--evidence" : ""}`}>{it}</span>)}</div>}
            </div>
          ))}
        </div>
      </div>
      <figcaption className="cv-env__foot">{caption && <span className="cv-dg__caption">{caption}</span>}{legend && <DiagramLegend items={["release"]} />}</figcaption>
    </figure>
  );
}
