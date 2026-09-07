import React from "react";
import { ReleaseEnvelope, RELEASE_ENVELOPE_CAPTION } from "./ReleaseEnvelope.jsx";
import { DiagramArrow } from "./DiagramArrow.jsx";

/** Trained model → robot-policy release → robot / controller. orientation="horizontal" for full-width figures (stacks ≤900px via CSS); "vertical" for column placement such as the hero split. Default vertical. */
export function ReleaseComposition({ orientation = "vertical", title = "The robot-policy release", caption = RELEASE_ENVELOPE_CAPTION, left = { title: "Trained model", sub: "from your training pipeline", ownership: "your team" }, right = { title: "Robot and controller", sub: "existing controller · safety system", ownership: "outside Convoy" } }) {
  const capId = React.useId();
  const descId = React.useId();
  const vertical = orientation === "vertical";
  return (
    <figure className="cv-dg" style={{ margin: 0 }} aria-describedby={`${descId} ${capId}`}>
      {title && <div className="cv-dg__title">{title}</div>}
      <p id={descId} className="cv-sr-only">A trained model, delivered by your team, enters the robot-policy release. The release is the dashed boundary Convoy is building: release identity, model assets, input and action processing, runtime, target configuration, and evaluation evidence. The release hands processed actions to the robot and its existing controller and safety system, which remain outside Convoy.</p>
      <div className={`cv-comp${vertical ? " cv-comp--vertical" : ""}`}>
        <div className="cv-comp__side"><div className="cv-dg__node cv-dg__node--external">{left.title}{left.sub && <small>{left.sub}</small>}</div><div className="cv-comp__ownership">{left.ownership}</div></div>
        <div className="cv-comp__arrow cv-path__arrow cv-path__arrow--release" aria-hidden="true"><ArrowSvg dash="6 4" /></div>
        <ReleaseEnvelope caption="" />
        <div className="cv-comp__arrow cv-path__arrow" aria-hidden="true"><ArrowSvg /></div>
        <div className="cv-comp__side"><div className="cv-dg__node cv-dg__node--external">{right.title}{right.sub && <small>{right.sub}</small>}</div><div className="cv-comp__ownership">{right.ownership}</div></div>
      </div>
      {caption && <figcaption id={capId} className="cv-dg__caption" style={{ marginTop: "var(--space-16)" }}>{caption}</figcaption>}
    </figure>
  );
}

function ArrowSvg({ dash }) {
  return <svg viewBox="0 0 40 16" fill="none"><path d="M1 8h32" stroke="currentColor" strokeWidth="2" strokeDasharray={dash} strokeLinecap="round" /><path d="M29 3.5 34.5 8 29 12.5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
}
