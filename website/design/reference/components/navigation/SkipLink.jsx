import React from "react";

/** Visually hidden until focused; first focusable element on the page. */
export function SkipLink({ href = "#main", children = "Skip to content" }) {
  return <a className="cv-skip" href={href}>{children}</a>;
}
