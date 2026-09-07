import * as React from "react";

/** CTA cluster: one primary + one secondary at most. Inline ≥640px, stacked below; `stack` forces stacking. */
export interface ActionGroupProps {
  children: React.ReactNode;
  stack?: boolean;
  /** Small secondary line under the actions. */
  note?: React.ReactNode;
  className?: string;
}
export declare function ActionGroup(props: ActionGroupProps): JSX.Element;
