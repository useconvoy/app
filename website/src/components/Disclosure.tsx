import type { ReactNode } from "react";

/**
 * A native details/summary disclosure: keyboard support and visible state
 * come from the browser, which is the right first-release choice. The mark
 * is a plus that rotates to a cross, so state never depends on color.
 */
export function Disclosure({
  summary,
  children,
  open,
  summaryClassName = "type-h4 text-primary",
  className = "",
  name,
}: {
  summary: ReactNode;
  children: ReactNode;
  open?: boolean;
  summaryClassName?: string;
  className?: string;
  name?: string;
}) {
  return (
    <details className={`disclosure group ${className}`} open={open} name={name}>
      <summary className={`${summaryClassName} py-3`}>
        <span>{summary}</span>
        <DisclosureMark />
      </summary>
      <div className="pb-5 text-secondary">{children}</div>
    </details>
  );
}

export function DisclosureMark() {
  return (
    <svg
      className="disclosure-mark text-accent"
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}
