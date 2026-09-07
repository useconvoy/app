import * as React from "react";

/** Eyebrow + heading + lead stack that opens every landing section. Max width = prose (66ch). */
export interface SectionHeadingProps {
  /** Mono uppercase label above the heading. */
  eyebrow?: string;
  title: React.ReactNode;
  lead?: React.ReactNode;
  /** Heading level. Default "h2". */
  as?: "h1" | "h2" | "h3";
  id?: string;
  className?: string;
}
export declare function SectionHeading(props: SectionHeadingProps): JSX.Element;
