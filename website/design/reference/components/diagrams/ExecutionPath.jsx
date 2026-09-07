import React from "react";
import { DiagramArrow, DiagramLegend } from "./DiagramArrow.jsx";

const DEFAULT_NODES = [
  { title: "Sensors", sub: "cameras · joints · force", site: "on the robot", scope: "outside" },
  { title: "Input processing", sub: "align · normalise", site: "placement varies", optional: true, scope: "convoy" },
  { title: "Model", sub: "learned policy", site: "placement varies", optional: true, scope: "convoy", model: true },
  { title: "Action processing", sub: "translate · limit", site: "near the controller", scope: "convoy" },
  { title: "Robot controller", sub: "controller · safety system", site: "on the robot", scope: "outside" },
];

export const EXECUTION_PATH_CAPTION = "Execution placement depends on the task’s compute, timing, and failure requirements.";

/** Sensors → Input processing → Model → Action processing → Robot controller. Convoy-scoped nodes sit inside a labelled dashed boundary that is real DOM (not aria-hidden) and survives the vertical layout. */
export function ExecutionPath({ nodes = DEFAULT_NODES, scopeLabel = "Convoy runtime boundary", legend = true, traceable = false, caption = EXECUTION_PATH_CAPTION, title }) {
  const [active, setActive] = React.useState(-1);
  const timer = React.useRef();
  const descId = React.useId();
  const capId = React.useId();
  const reduced = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const trace = () => {
    clearTimeout(timer.current);
    if (reduced) { setActive(active === nodes.length ? -1 : nodes.length); return; }
    let i = 0;
    const step = () => { setActive(i); if (i < nodes.length) { i += 1; timer.current = setTimeout(step, 520); } };
    step();
  };
  React.useEffect(() => () => clearTimeout(timer.current), []);
  const lit = (i) => active >= 0 && (active === nodes.length || i <= active);
  // group consecutive nodes by scope so the boundary wraps the Convoy-scoped run
  const groups = [];
  nodes.forEach((n, i) => { const g = groups[groups.length - 1]; if (g && g.scope === n.scope) g.items.push(i); else groups.push({ scope: n.scope, items: [i] }); });
  const node = (i) => { const n = nodes[i]; return (
    <div className={`cv-path__node${lit(i) ? " is-lit" : ""}`} key={n.title}>
      <div className={["cv-dg__node", n.scope === "outside" ? "cv-dg__node--external" : "", n.model ? "cv-dg__node--model" : ""].filter(Boolean).join(" ")}>{n.title}{n.sub && <small>{n.sub}</small>}</div>
      <div className={`cv-path__site${n.optional ? " cv-path__site--optional" : ""}`}>{n.site}</div>
    </div>
  ); };
  const arrow = (i) => <DiagramArrow key={`a${i}`} kind="execution" />;
  const description = `${nodes.map((n) => n.title).join(", then ")}. ${scopeLabel}: ${nodes.filter((n) => n.scope === "convoy").map((n) => n.title).join(", ")}. Outside Convoy: ${nodes.filter((n) => n.scope === "outside").map((n) => n.title).join(" and ")}.`;
  return (
    <figure className="cv-dg" style={{ margin: 0 }} aria-describedby={`${descId} ${capId}`}>
      {title && <div className="cv-dg__title">{title}</div>}
      <p id={descId} className="cv-sr-only">{description}</p>
      <div className="cv-path">
        {groups.map((g, gi) => (
          <React.Fragment key={gi}>
            <div className={`cv-path__group cv-path__group--${g.scope === "convoy" ? "scope" : "external"}`} data-label={g.scope === "convoy" ? scopeLabel : undefined} role={g.scope === "convoy" ? "group" : undefined} aria-label={g.scope === "convoy" ? scopeLabel : undefined}>
              {g.items.map((i, k) => <React.Fragment key={i}>{node(i)}{k < g.items.length - 1 && arrow(i)}</React.Fragment>)}
            </div>
            {gi < groups.length - 1 && arrow(`g${gi}`)}
          </React.Fragment>
        ))}
      </div>
      <figcaption className="cv-dg__foot">
        {caption && <span id={capId} className="cv-dg__caption">{caption}</span>}
        {legend && <DiagramLegend items={["execution", "release", "optional"]} />}
        {traceable && <button type="button" className="cv-btn cv-btn--secondary cv-btn--sm" onClick={trace} aria-pressed={active >= 0}>{active >= 0 ? "Trace again" : "Trace execution"}</button>}
      </figcaption>
    </figure>
  );
}
