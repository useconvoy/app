import * as React from "react";

/** Native <details>/<summary> disclosure for FAQ and boundaries. Keyboard and screen-reader behaviour come from the browser. */
export interface DisclosureProps {
  summary: React.ReactNode;
  children: React.ReactNode;
  open?: boolean;
  /** Shared name makes a group exclusive (one open at a time). Omit to allow several open answers (landing default). */
  name?: string;
  /** Smaller summary (16px) for in-step detail. */
  compact?: boolean;
}
export declare function Disclosure(props: DisclosureProps): JSX.Element;
