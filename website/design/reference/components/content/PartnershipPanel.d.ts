import * as React from "react";

/** White panel with heading/lead/actions and a labelled fit list. 7/5 grid ≥900px, stacked below. */
export interface PartnershipPanelProps {
  eyebrow?: string;
  title: React.ReactNode;
  lead?: React.ReactNode;
  /** Heading id (for aria-labelledby). */
  id?: string;
  actions?: React.ReactNode;
  fit?: { label: string; body: string }[];
}
export declare function PartnershipPanel(props: PartnershipPanelProps): JSX.Element;
