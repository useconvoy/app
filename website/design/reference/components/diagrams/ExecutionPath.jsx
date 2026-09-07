import React from "react";
import { DiagramArrow, DiagramLegend } from "./DiagramArrow.jsx";

const DEFAULT_NODES = [
  { title: "Sensors", sub: "cameras · joints · force", site: "robot", external: true },
  { title: "Input processing", sub: "sync · normalise", site: "local · site · cloud", optional: true },
  { title: "Model", sub: "learned policy", site: "local · site · cloud", optional: true, model: true },
  { title: "Action processing", sub: "limits · rates", site: "local", optional: false },
  { title: "Robot controller", sub: "outside Convoy", site: "robot · safety", external: true },
];

export function ExecutionPath({ nodes = DEFAULT_NODES, scope = "Convoy runtime scope · placement is configuration-dependent", legend = true, traceable = false, caption = "Conceptual execution path. Placement of processing and model differs per configuration; repeatability of the release, not identical physical behaviour, is the goal." }) {
  const [active, setActive] = React.useState(-1);
  const timer = React.useRef();
  const reduced = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const trace = () => {
    clearTimeout(timer.current);
    if (reduced) { setActive(active === nodes.length ? -1 : nodes.length); return; }
    let i = 0;
    const step = () => { setActive(i); if (i < nodes.length) { i += 1; timer.current = setTimeout(step, 520); } };
    step();
  };
  React.useEffect(() => () => clearTimeout(timer.current), []);
  const lit = (i) => active === nodes.length || i <= active;
  return (
    <figure className="cv-dg" style={{ margin: 0 }}>
      <div className="cv-path">
        {nodes.map((n, i) => (
          <React.Fragment key={n.title}>
            <div className="cv-path__node">
              <div className={["cv-dg__node", n.external ? "cv-dg__node--external" : "", n.model ? "cv-dg__node--model" : ""].filter(Boolean).join(" ")} style={active >= 0 ? { borderColor: lit(i) ? "var(--diagram-release)" : undefined, boxShadow: lit(i) ? "0 0 0 2px var(--color-accent-tint)" : undefined, transition: "border-color var(--dur-slow) var(--ease-diagram), box-shadow var(--dur-slow) var(--ease-diagram)" } : undefined}>
                {n.title}{n.sub && <small>{n.sub}</small>}
              </div>
              <div className={`cv-path__site${n.optional ? " cv-path__site--optional" : ""}`}>{n.site}</div>
            </div>
            {i < nodes.length - 1 && <DiagramArrow kind="execution" />}
          </React.Fragment>
        ))}
      </div>
      {scope && (
        <div className="cv-path__scope" aria-hidden="true">
          <div className="cv-path__scope-bar" style={{ left: "calc(20% + 20px)", right: "calc(20% + 20px)" }}><span className="cv-path__scope-label">{scope}</span></div>
        </div>
      )}
      <figcaption style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-12) var(--space-24)", justifyContent: "space-between", alignItems: "center", marginTop: "var(--space-16)" }}>
        {caption && <span className="cv-dg__caption" style={{ flex: "1 1 320px" }}>{caption}</span>}
        {legend && <DiagramLegend items={["execution", "release", "optional"]} />}
        {traceable && <button type="button" className="cv-btn cv-btn--secondary cv-btn--sm" onClick={trace} aria-pressed={active >= 0}>{active >= 0 ? "Trace again" : "Trace execution"}</button>}
      </figcaption>
    </figure>
  );
}
