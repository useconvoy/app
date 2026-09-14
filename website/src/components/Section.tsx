import type { ReactNode } from "react";

/** Shared section rhythm and page measure, with optional semantic color bands. */
export function Section({
  id,
  labelledBy,
  children,
  className = "",
}: {
  id: string;
  labelledBy: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      id={id}
      aria-labelledby={labelledBy}
      className={`page-section ${className}`}
    >
      <div className="section-inner">{children}</div>
    </section>
  );
}

export function SectionHeading({
  id,
  eyebrow,
  heading,
  lead,
  children,
}: {
  id: string;
  eyebrow: string;
  heading: string;
  lead?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="section-heading">
      <p className="type-eyebrow">{eyebrow}</p>
      <h2 id={id} className="type-h2 mt-3 text-primary">
        {heading}
      </h2>
      {lead ? <p className="type-lead mt-4 text-secondary">{lead}</p> : null}
      {children}
    </div>
  );
}
