import React from "react";

const Arrow = () => (
  <svg className="cv-btn__arrow" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3 8h9.5M9 3.5 13.5 8 9 12.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
);

export function Button({ variant = "primary", size = "md", block = false, arrow = false, href, disabled = false, type = "button", className = "", children, ...rest }) {
  const cls = ["cv-btn", `cv-btn--${variant}`, size === "sm" ? "cv-btn--sm" : "", block ? "cv-btn--block" : "", className].filter(Boolean).join(" ");
  if (href && !disabled) {
    return <a className={cls} href={href} {...rest}>{children}{arrow && <Arrow />}</a>;
  }
  return <button className={cls} type={type} disabled={disabled} {...rest}>{children}{arrow && <Arrow />}</button>;
}
