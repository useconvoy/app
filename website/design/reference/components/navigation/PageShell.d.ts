import * as React from "react";

/** Page shell: header slot, <main id="main"> (skip-link target), footer slot. */
export interface PageShellProps {
  header?: React.ReactNode;
  footer?: React.ReactNode;
  children: React.ReactNode;
  mainId?: string;
}
export declare function PageShell(props: PageShellProps): JSX.Element;

/** Section container with max-width, page padding and section gap. */
export interface SectionProps {
  id?: string;
  /** id of the heading inside; sets aria-labelledby. */
  labelledBy?: string;
  /** Hairline top rule. Default true. */
  rule?: boolean;
  /** Two-column layout: "split" (5/7), "wide-left" (7/5) or "even". Collapses to one column ≤900px (even: ≤640px). */
  grid?: "split" | "wide-left" | "even";
  className?: string;
  as?: "section" | "div";
  children: React.ReactNode;
}
export declare function Section(props: SectionProps): JSX.Element;
