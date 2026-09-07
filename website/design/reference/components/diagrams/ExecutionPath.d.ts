import * as React from "react";

/** Five-node execution path. Horizontal ≥640px, vertical below, with the Convoy runtime boundary drawn as a labelled dashed group in both layouts and described for screen readers. */
export interface ExecutionPathProps {
  nodes?: { title: string; sub?: string; site?: string; optional?: boolean; model?: boolean; scope: "convoy" | "outside" }[];
  /** Label of the dashed boundary around Convoy-scoped nodes. */
  scopeLabel?: string;
  legend?: boolean;
  /** Adds a user-triggered "Trace execution" button that highlights nodes in order (static complete state under reduced motion). */
  traceable?: boolean;
  caption?: string;
  title?: string;
}
export declare function ExecutionPath(props: ExecutionPathProps): JSX.Element;
export declare const EXECUTION_PATH_CAPTION: string;
