import React from "react";

export function StatusBadge({ tone = "neutral", dot = true, children, className = "" }) {
  return <span className={`cv-badge cv-badge--${tone} ${className}`.trim()}>{dot && <span className="cv-badge__dot" aria-hidden="true" />}{children}</span>;
}
