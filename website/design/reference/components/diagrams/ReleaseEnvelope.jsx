import React from "react";

const DEFAULT_GROUPS = [
  { key: "model", title: "Model assets", note: "the trained policy", items: ["policy weights", "preprocessing spec", "action space"] },
  { key: "processing", title: "Input and action processing", note: "how signals are shaped", items: ["sensor alignment", "normalisation", "command translation"] },
  { key: "runtime", title: "Runtime", note: "what executes it", items: ["inference loop", "dependencies", "timing budget"] },
  { key: "target", title: "Target configuration", note: "where it runs", items: ["robot configuration", "compute placement", "controller interface"] },
  { key: "evidence", title: "Evaluation evidence", note: "why it may be released", items: ["task criteria", "runtime criteria", "tested configuration", "conditions and results"], evidence: true, wide: true },
];

export const RELEASE_ENVELOPE_CAPTION = "Conceptual architecture: a robot-policy release brings together the model, input and action processing, runtime, target configuration, and evaluation evidence.";

/** The recurring brand device. Tag reads "Robot-policy release"; identity group replaces any version number. No fabricated values. */
export function ReleaseEnvelope({ tag = "Robot-policy release", identity = "Release identity", identityNote = "name · configuration · evidence reference", groups = DEFAULT_GROUPS, caption = RELEASE_ENVELOPE_CAPTION, title, describedBy }) {
  const capId = React.useId();
  return (
    <figure className="cv-dg" style={{ margin: 0 }} aria-describedby={caption ? capId : describedBy}>
      {title && <div className="cv-dg__title">{title}</div>}
      <div className="cv-env">
        <div className="cv-env__tag">{tag}</div>
        <div className="cv-env__grid">
          {identity && <div className="cv-env__group cv-env__group--wide cv-env__group--identity"><h4>{identity}{identityNote && <small>{identityNote}</small>}</h4></div>}
          {groups.map((g) => (
            <div key={g.key || g.title} className={["cv-env__group", g.wide ? "cv-env__group--wide" : "", g.evidence ? "cv-env__group--evidence" : ""].filter(Boolean).join(" ")}>
              <h4>{g.title}{g.note && <small>{g.note}</small>}</h4>
              {g.items && <ul className="cv-env__items" style={{ listStyle: "none", margin: 0, padding: 0 }}>{g.items.map((it) => <li key={it} className={`cv-env__item${g.evidence ? " cv-env__item--evidence" : ""}`}>{it}</li>)}</ul>}
            </div>
          ))}
        </div>
      </div>
      {caption && <figcaption id={capId} className="cv-dg__caption" style={{ marginTop: "var(--space-16)" }}>{caption}</figcaption>}
    </figure>
  );
}
