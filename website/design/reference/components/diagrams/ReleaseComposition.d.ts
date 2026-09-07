import * as React from "react";

/** Hero figure: trained model → release envelope → robot and controller, with ownership labels and a screen-reader relationship description. */
export interface ReleaseCompositionProps {
  /** "vertical" (default) for column placement, e.g. the hero split; "horizontal" only for full-width figures ≥900px (stacks below via CSS). */
  orientation?: "vertical" | "horizontal";
  title?: string;
  caption?: string;
  left?: { title: string; sub?: string; ownership?: string };
  right?: { title: string; sub?: string; ownership?: string };
}
export declare function ReleaseComposition(props: ReleaseCompositionProps): JSX.Element;
