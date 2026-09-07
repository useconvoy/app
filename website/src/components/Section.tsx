import type { ReactNode } from "react";

/**
 * One page section on flat paper: a hairline rule above, the 1280px
 * measure, page padding 20 / 32 / 40 / 64 and section gaps 40 / 56 / 80.
 * Sections are separated by rules, never by colour bands; where a block
 * needs a container it gets a bordered white surface inside the section.
 */
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
      className={`border-t border-subtle bg-background py-10 sm:py-14 lg:py-20 ${className}`}
    >
      <div className="mx-auto w-full max-w-(--content-max) px-5 sm:px-8 md:px-10 lg:px-16">{children}</div>
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
    <div className="max-w-[820px]">
      <p className="type-eyebrow">{eyebrow}</p>
      <h2 id={id} className="type-h2 mt-3 text-primary">
        {heading}
      </h2>
      {lead ? <p className="type-lead mt-4 text-secondary">{lead}</p> : null}
      {children}
    </div>
  );
}
