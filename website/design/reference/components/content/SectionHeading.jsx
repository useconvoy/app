import React from "react";

export function SectionHeading({ eyebrow, title, lead, as = "h2", id, className = "" }) {
  const Tag = as;
  return (
    <div className={`cv-sh ${className}`.trim()}>
      {eyebrow && <p className="cv-eyebrow">{eyebrow}</p>}
      <Tag id={id} className={as === "h1" ? "cv-h1" : as === "h3" ? "cv-h3" : "cv-h2"}>{title}</Tag>
      {lead && <p className="cv-sh__lead">{lead}</p>}
    </div>
  );
}
