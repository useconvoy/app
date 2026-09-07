import React from "react";

export function Notice({ tone = "neutral", children, role }) {
  return <div className={`cv-notice cv-notice--${tone}`} role={role || (tone === "error" ? "alert" : "status")}><span className="cv-notice__dot" aria-hidden="true" />{children}</div>;
}
